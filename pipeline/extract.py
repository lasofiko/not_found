"""Извлечение подписанных ячеек HTML и каталога XLSX без угадывания чисел."""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from io import BytesIO
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile


def clean(value: str) -> str:
    return " ".join(value.split())


def mass(value: str, unit: str) -> str:
    """Прочерк не интерпретируется как ноль; специальные коды требуют паспорта."""
    if clean(value) in {"", "–", "—", "-"}:
        return ""
    factors = {"тонн": Decimal(1), "тыс. тонн": Decimal(1000), "млн тонн": Decimal(1000000)}
    if unit not in factors:
        raise ValueError(f"Нет допустимого пересчёта массы из {unit}")
    text = re.sub(r"\s", "", value).replace(",", ".")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", text):
        raise ValueError(f"Неоднозначное число или специальный код: {value!r}")
    try:
        number = Decimal(text) * factors[unit]
    except InvalidOperation as error:
        raise ValueError(f"Не удалось прочитать массу: {value!r}") from error
    if not number.is_finite() or number < 0:
        raise ValueError("Масса должна быть конечной и неотрицательной")
    return format(number, "f")


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def text(self) -> str:
        if self.tag in {"script", "style"}:
            return ""
        return clean(" ".join(c.text() if isinstance(c, Node) else c for c in self.children))

    def find_all(self, tag: str, **attrs) -> list:
        nodes = []
        for child in self.children:
            if isinstance(child, Node):
                if child.tag == tag and all(child.attrs.get(k) == v for k, v in attrs.items()):
                    nodes.append(child)
                nodes.extend(child.find_all(tag, **attrs))
        return nodes


class Document(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def table_rows(table: Node) -> list[list[str]]:
    return [[c.text() for c in row.children if isinstance(c, Node) and c.tag in {"td", "th"}]
            for row in table.find_all("tr")]


def extract_freight(html: str, years=range(2015, 2024)) -> tuple[list[dict], list[dict]]:
    root = Document(html).root
    line, terminal = [], []
    for year in years:
        panels = root.find_all("div", id=f"{year}y")
        if len(panels) != 1:
            raise ValueError(f"Ожидалась одна годовая вкладка #{year}y; получено {len(panels)}")
        tables = panels[0].find_all("table")
        if len(tables) != 1:
            raise ValueError(f"Неоднозначная структура годовой таблицы {year}")
        section, counts = None, {}
        for cells in table_rows(tables[0]):
            if len(cells) == 1:
                if "Паромная линия" in cells[0] and "Усть-Луга" in cells[0] and "Калининград" in cells[0]:
                    section = "line"
                elif "Железнодорожный паромный комплекс в морском порту Калининград" in cells[0]:
                    section = "terminal"
                else:
                    section = None
                continue
            if len(cells) != 2:
                continue
            label, raw = cells
            if section == "line" and label in {"Выгружено (тонн)", "Погружено (тонн)"}:
                counts[label] = counts.get(label, 0) + 1
                line.append({"period": str(year), "label": label, "original_value": raw,
                             "value_tonnes": mass(raw, "тонн"),
                             "source_locator": f"#{year}y / Паромная линия / {label}"})
            if section == "terminal" and label in {"ВСЕГО (тонн)", "Каботаж (тонн)"}:
                counts[label] = counts.get(label, 0) + 1
                terminal.append({"period": str(year), "label": label, "original_value": raw,
                                 "value_tonnes": mass(raw, "тонн"),
                                 "source_locator": f"#{year}y / ЖДПК / {label}"})
        if counts != {label: 1 for label in ("Выгружено (тонн)", "Погружено (тонн)", "ВСЕГО (тонн)", "Каботаж (тонн)")}:
            raise ValueError(f"Отсутствуют или повторяются подписанные строки {year}: {counts}")
    return line, terminal


def extract_ferry(html: str) -> list[dict]:
    matches = []
    for table in Document(html).root.find_all("table"):
        text = table.text()
        if "Год" in text and "перевезенных грузов" in text and "тыс. тонн" in text:
            matches.append(table_rows(table))
    if len(matches) != 2:
        raise ValueError(f"Ожидались две таблицы перевозок филиала, найдено {len(matches)}")
    result = []
    for index, rows in enumerate(matches):
        seen = set()
        for cells in rows:
            if not cells or not re.fullmatch(r"20[0-9]{2}", cells[0]):
                continue
            expected_width = 5 if index == 0 else 3
            if len(cells) != expected_width or cells[0] in seen:
                raise ValueError("Изменилась структура таблицы перевозок филиала")
            seen.add(cells[0])
            raw = cells[3 if index == 0 else 2]
            result.append({"series_id": "ferry_branch_main" if index == 0 else "ferry_branch_spb_call",
                           "period": cells[0], "original_value": raw,
                           "value_tonnes": mass(raw, "тыс. тонн"),
                           "source_locator": f"Статистическая информация / таблица {index + 1} / {cells[0]} / тыс. тонн"})
        if not seen:
            raise ValueError("Нет годовых строк в таблице перевозок филиала")
    return result


def catalog(payload: bytes) -> list[dict]:
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rid = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    with ZipFile(BytesIO(payload)) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ["".join(t.text or "" for t in si.findall(".//s:t", ns))
                       for si in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", ns)]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        rels = {r.attrib["Id"]: r.attrib["Target"] for r in ET.fromstring(
            archive.read("xl/worksheets/_rels/sheet1.xml.rels"))}
        links = {h.attrib["ref"]: rels[h.attrib[rid]] for h in sheet.findall("s:hyperlinks/s:hyperlink", ns)}
        result = []
        for row in sheet.findall("s:sheetData/s:row", ns):
            index = int(row.attrib["r"])
            if index == 1:
                continue
            cells = {}
            for cell in row.findall("s:c", ns):
                val = cell.find("s:v", ns)
                value = val.text if val is not None else ""
                if cell.attrib.get("t") == "s" and value:
                    value = strings[int(value)]
                elif cell.attrib.get("t") == "inlineStr":
                    value = "".join(t.text or "" for t in cell.findall(".//s:t", ns))
                cells[cell.attrib["r"]] = value
            title = cells.get(f"A{index}", "")
            if not title:
                continue
            url = links.get(f"A{index}", "")
            if not url.startswith(("http://", "https://")):
                raise ValueError(f"Нет ссылки источника в A{index}")
            purpose, priority = "reference", "later"
            if any(token in title for token in ("ВРП", "ЕМИСС", "Банк России", "погода", "Open-Meteo", "Росгидромет", "календарь")):
                purpose = "factor"
            if any(token in title for token in ("характеристики портов", "мощности портов")):
                purpose = "scenario_parameter"
            if "грузовые перевозки" in title:
                purpose, priority = "target_candidate", "2"
            elif any(token in title for token in ("АСОП", "транспорт регионов", "характеристики портов")):
                priority = "2" if "характеристики" not in title else "3"
            elif any(token in title for token in ("ВРП", "ЕМИСС", "Росстат: регионы")):
                priority = "4"
            result.append({"catalog_id": f"catalog_{index - 1:02d}", "title": title,
                           "description": cells.get(f"B{index}", ""), "tracks": cells.get(f"C{index}", ""),
                           "url": url, "purpose": purpose, "priority": priority,
                           "review_status": "discovered",
                           "limitations": "Описание из каталога; файлы и соответствие коридору не подтверждены."})
    if not result:
        raise ValueError("Каталог пуст или изменилась структура XLSX")
    return result

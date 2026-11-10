"""Новые источники: единицы, охват, пропуски и подписи на синтетических данных."""
import csv
import importlib.util
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
from zipfile import ZipFile

from pipeline.acquire_list import validate_download
from pipeline.extended_extract import airport_pdf, airport_xlsx, belarus_transit, border_workbook, customs_history, regional_archive, weather
from pipeline.supplemental import parse_port


class ExtendedSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "source"
        self.artifact = {"source_id": "synthetic", "url": "https://example.test", "original_filename": "synthetic"}

    def test_real_html_form_captcha_script_is_not_a_block_page(self):
        self.path.write_text('<html><head><title>Расписание</title><script src="captcha.js"></script></head><body>Расписание</body></html>', encoding="utf-8")
        validate_download(self.path, "html")
        self.path.write_text('<html><title>Just a moment...</title>Verify you are human</html>', encoding="utf-8")
        with self.assertRaises(ValueError):
            validate_download(self.path, "html")
        self.path.write_text('<html><title>Данные</title>Таблицы</html>', encoding="utf-8")
        with self.assertRaises(ValueError):
            validate_download(self.path, "pdf")

    def test_regional_missing_code_does_not_remove_metadata_cd_and_other_regions(self):
        headers = ['section','indicator_code','indicator_name','subsection','object_name','object_level','object_oktmo','object_okato','year','indicator_value','indicator_unit','comment','source','version_date']
        row = ['Транспорт','X','Масса','CD','Калининградская область','Регион','27','27','2023','1.5','Миллионов тонн','ND','Издание','v1']
        stream = io.StringIO()
        w = csv.writer(stream, delimiter=';');w.writerow(headers);w.writerow(row)
        row[8]='2024';row[9]='-99999999';w.writerow(row)
        row[4]='Другая область';row[9]='999';w.writerow(row)
        with ZipFile(self.path, 'w') as z:z.writestr('data.csv',stream.getvalue().encode())
        rows = regional_archive(self.path, self.artifact, 'test')
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['value'],'1500000')
        self.assertEqual(rows[0]['subsection'],'CD')
        self.assertEqual(rows[1]['value'],'')
        self.assertEqual(rows[1]['original_value'],'-99999999')

    def test_weather_grid_differs_from_requested_and_negative_temperature_is_valid(self):
        self.artifact.update(url='https://example.test?latitude=54.71&longitude=20.45',original_filename='Kaliningrad.csv')
        self.path.write_text('latitude,longitude,timezone\n54.72,20.46,Europe/Moscow\n\ntime,precipitation_sum (mm),wind_speed_10m_max (km/h),temperature_2m_mean (°C)\n2024-02-29,0,10,-5\n',encoding='utf-8')
        rows=weather(self.path,self.artifact,'test')
        self.assertEqual(len(rows),3)
        self.assertEqual(rows[-1]['value'],'-5')
        self.assertEqual(rows[-1]['requested_latitude'],'54.71')
        self.assertEqual(rows[-1]['grid_latitude'],'54.72')
        self.path.write_text(self.path.read_text(encoding="utf-8")+'2024-02-29,0,10,-5\n', encoding="utf-8")
        with self.assertRaises(ValueError):weather(self.path,self.artifact,'test')

    def test_border_2026_does_not_invent_months_after_september(self):
        rows={1:{'A':'samochody ciężarowe; styczeń - wrzesień 2026 r.'},
              4:{'D':'styczeń','L':'wrzesień','M':'październik'},
              5:{'B':'Bezledy – Bagrationowsk','C':'przyjazd do RP','D':'0','L':'12','M':''}}
        with patch('pipeline.extended_extract.read_xlsx',return_value=[('sam. ciężarowe',rows)]):
            result=border_workbook(self.path,self.artifact,'test')
        self.assertEqual([r['period'] for r in result],['2026-01','2026-09'])
        self.assertEqual(result[0]['value'],'0')
        self.assertEqual(result[0]['unit'],'truck_crossings')
        self.assertEqual(result[0]['direction_scope'],'unknown')
        rows[1]['A']='samochody ciężarowe; dane w roku 2026'
        rows[5]['M']='0'
        with patch('pipeline.extended_extract.read_xlsx',return_value=[('sam. ciężarowe',rows)]):
            result=border_workbook(self.path,{**self.artifact,'original_filename':'PL_border_trucks_2026_Jan_Sep.xlsx'},'test')
        self.assertEqual([r['period'] for r in result],['2026-01','2026-09'])

    def test_customs_cp866_is_decoded_but_unknown_netto_unit_not_converted(self):
        content='NAPR,PERIOD,STRANA,TNVED,EDIZM,STOIM,NETTO,KOL,REGION,REGION_S\r\nЭК,2013,DE,2204,,100,50,0,27000 - КАЛИНИНГРАДСКАЯ ОБЛАСТЬ,02 - СЕВЕРО-ЗАПАД\r\n'
        with ZipFile(self.path,'w') as z:z.writestr('TCBT.csv',content.encode('cp866'))
        rows=customs_history(self.path,self.artifact,'test')
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['flow_label'],'ЭК')
        self.assertEqual(rows[0]['netto_original'],'50')
        self.assertEqual(rows[0]['value_tonnes'],'')

    def test_airport_xlsx_excludes_passengers_and_annual_total(self):
        rows={3:{'B':'Груз, всего, тонн'},4:{'B':'2018 Январь','C':'2018 Янв - Дек'},5:{'A':'Калининград(Храброво)','B':'1.5','C':'999'}}
        passenger={**rows,3:{'B':'Пассажиры, человек'},5:{'A':'Калининград(Храброво)','B':'100000','C':'900000'}}
        with patch('pipeline.extended_extract.read_xlsx',return_value=[('пассажиры',passenger),('груз',rows)]):
            result=airport_xlsx(self.path,self.artifact,'test')
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['value'],'1.5')
        self.assertEqual(result[0]['direction_scope'],'both')

    def test_belarus_uses_only_transit_and_keeps_ambiguous_cell_missing(self):
        table='<table><tr><td>Год</td>'+''.join(f'<td>{m}</td>' for m in ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'])+'</tr>'
        table+=''.join('<tr><td>'+year+'</td>'+''.join('<td>'+('327.442' if year=='2023' and m==10 else '123')+'</td>' for m in range(1,13))+'</tr>' for year in ['2022','2023','2024'])+'</table>'
        self.path.write_text('<html><p>15.2.1. Ввоз (импорт):</p>'+table+'<p>15.2.3. Транзит в Калининградскую область:</p>'+table+'<p>15.2.4. Транзит из Калининградской области:</p>'+table+'</html>', encoding="utf-8")
        result,_=belarus_transit(self.path,self.artifact,'test')
        self.assertEqual(len(result),72)
        disputed=[r for r in result if r['period']=='2023-10']
        self.assertTrue(all(r['value']=='' and r['original_value']=='327.442' for r in disputed))
        self.assertEqual({r['direction_scope'] for r in result},{'inbound','outbound'})

    def test_historical_port_offset_preserves_original_cell_location(self):
        rows={3:{'B':'Объём перевалки грузов за январь 2019 г.'},4:{'B':'каботаж','Z':'тыс. тонн'},
              5:{'B':'Бассейн / Порт','C':'Год','D':'Всего\nгрузов'},6:{'B':'Порт Калининград'},
              7:{'B':'Итого','C':'2019','D':'1.25'}}
        result=parse_port([('Лист1',rows)],{'source_id':'synthetic','publication_year':2019,'publication_month':1},'test')
        self.assertEqual(result[0]['value'],'1250')
        self.assertEqual(result[0]['source_locator'],'Лист1!D7')
        rows[3]['B']='Объём перевалки грузов за февраль 2019 г.'
        with self.assertRaises(ValueError):
            parse_port([('Лист1',rows)],{'source_id':'synthetic','publication_year':2019,'publication_month':2},'test')

    @unittest.skipUnless(importlib.util.find_spec('pymupdf'),'нужны зависимости data для PDF')
    def test_airport_pdf_uses_mass_section_and_decimal_values(self):
        import pymupdf
        # Проверяем разобранный текст страницы, не создавая синтетику в data/raw.
        class Page:
            def get_text(self,**kwargs):return 'Груз, всего, тонн\nКалининград(Храброво) '+' '.join(['1,00']*12+['12,00'])
        class Doc:
            def __enter__(self):return [Page()]
            def __exit__(self,*args):pass
        self.artifact['original_filename']='airport_2020.pdf'
        with patch('pymupdf.open',return_value=Doc()):
            rows=airport_pdf(self.path,self.artifact,'test')
        self.assertEqual(len(rows),12)
        self.assertEqual({r['value'] for r in rows},{'1'})


if __name__ == '__main__':unittest.main()

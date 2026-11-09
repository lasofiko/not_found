# Запуск годовой оценки и прогноза

Нужны Python 3.11+, код ветки и архив `data-handoff.zip` с файлом `data-handoff.zip.sha256`. В самом репозитории данных нет.

## Команды

Архив распаковывается в отдельную папку, затем в неё копируется `ml/`. `pipeline/` из репозитория копировать не нужно: `pipeline.verify` сверяет хеши кода из комплекта.

Windows PowerShell, папка с коротким путём:

```powershell
cd C:\nf
Get-FileHash .\data-handoff.zip -Algorithm SHA256
Get-Content .\data-handoff.zip.sha256
Expand-Archive .\data-handoff.zip -DestinationPath .\bundle
cd .\bundle
Copy-Item -Recurse C:\путь\к\репозитории\ml .
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[data,ml]"
python -m pipeline.verify
python -m ml.run --end-year 2030
```

Linux и macOS:

```bash
cd ~/nf
sha256sum -c data-handoff.zip.sha256
unzip data-handoff.zip -d bundle
cd bundle
cp -r /путь/к/репозитории/ml .
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[data,ml]"
python -m pipeline.verify
python -m ml.run --end-year 2030
```

После работы `pipeline.verify` печатает «Комплект проверен…», а `ml.run` — выбранные методы и путь к отчёту.

## Результаты

Папка `reports/generated/ml/<model_version>/`:

| Файл | Содержание |
|---|---|
| `report.html` | Обзор с графиками, откройте в браузере |
| `backtest.csv` | Обучающий и проверочный период, направление, факт, прогноз, ошибки, версии |
| `metrics.json` | MAE, WAPE, определения, роли окон, сравнение с baseline |
| `model-card.md` | Метод, покрытие, качество относительно baseline, интервалы, что нельзя публиковать |
| `forecast.csv`, `forecast.json` | Прогнозы 2024–2030 с версиями и предупреждениями |
| `break_2022_2023.csv` | Ошибки методов в годы перелома |
| `external_terminal_check.csv` | Внешняя сверка суммы линии со строкой терминала |
| `inbound.png`, `outbound.png` | Факты, прогнозы и временная проверка |
| `run_manifest.json` | Хеши выходов и версии |

Модель сохраняется в `models/<model_version>/model.json`, указатель — `models/latest.json`. Папки `models/` и `reports/generated/` в Git не попадают.

`model_version` зависит от версий данных, кода и библиотек, поэтому на другой машине может отличаться. Для данных `bb-data-9b14bc321b2e` метрики должны совпадать: MAE 2022–2023 у выбранных методов равна 289 531,96 т для `inbound` (`ses`) и 283 553,00 т для `outbound` (`naive`).

## Функция прогноза

```python
from ml import forecast
result = forecast(direction="inbound", end_year=2030)
```

| Параметр | Допустимые значения |
|---|---|
| `direction` | `inbound`, `outbound` |
| `end_year` | Целое от 2024 до 2030 |
| `root`, `model_path` | Необязательные: корень проекта и конкретный артефакт |

Срезы: только эта паромная линия, вид `sea`, год, все грузы без разбивки. Проверенный горизонт — один год; более дальние значения возвращаются с предупреждением. Ответ содержит `status: forecast`, `data_version`, `model_version`, единицу, точки с интервалами 80% и 95%, происхождение и предупреждения.

Функция отклоняет с сообщением на русском: неизвестное направление, неподдерживаемый год, отсутствующую или повреждённую модель (проверка SHA-256).

## Проверка недостатка данных и типичные ошибки

`ml.run` останавливается, если комплект не прошёл проверку, строк не ровно 18, есть пропуск, повтор, неверный OD, оценка или синтетика.

| Сообщение | Что делать |
|---|---|
| «Не совпадает SHA-256 результата или кода» | Распаковать архив заново, не заменять `pipeline/` |
| «Нет заполненного ряда из 18 наблюдений» | Запросить полный комплект у Data Engineer |
| «Установите ML-зависимости» | Активировать окружение и повторить установку |
| «No such file … build_manifest.json» | Команды запущены не из распакованного комплекта |

## Тесты

Из корня репозитория, данные не нужны:

```bash
python -m pip install -e ".[dev,data,ml]"
python -m pytest -q
```

Тесты используют синтетические примеры и не являются оценкой точности реального ряда.

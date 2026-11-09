const form = document.querySelector('#capacity-form');
const result = document.querySelector('#result');
const submit = form.querySelector('button[type="submit"]');
const fmt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
let apiBase;

async function loadConfig() {
  try {
    const response = await fetch(new URL('config.json', document.baseURI), { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const config = await response.json();
    const base = config.apiBase;
    if (typeof base !== 'string' ||
        !(base.startsWith('/') && !base.startsWith('//') || /^https?:\/\//i.test(base))) {
      throw new Error('Некорректный адрес API');
    }
    const url = new URL(base, window.location.origin);
    if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Некорректный адрес API');
    apiBase = url.href.replace(/\/+$/, '');
    submit.disabled = false;
    result.textContent = '';
  } catch {
    result.textContent = 'Не удалось загрузить настройки интерфейса. Проверьте config.json и доступность сервера.';
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!apiBase) return;
  const values = Object.fromEntries(new FormData(form));
  for (const key of ['demand_tonnes', 'capacity_tonnes', 'capacity_delta_tonnes']) values[key] = Number(values[key]);
  result.textContent = 'Расчёт…';
  try {
    let response;
    try {
      response = await fetch(`${apiBase}/scenarios/capacity`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values)
      });
    } catch {
      throw new Error('Не удалось связаться с API. Проверьте адрес сервера и настройки CORS.');
    }
    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error('Сервер вернул некорректный JSON-ответ.');
    }
    if (!response.ok) {
      const message = data?.error?.message || 'Запрос не выполнен';
      const details = data?.error?.details?.map((item) => item.message).join('; ');
      throw new Error(`Ошибка API (${response.status}): ${message}${details ? `. ${details}` : ''}`);
    }
    result.innerHTML = `<h2>Результат за ${escapeHtml(data.period)}</h2>
      <p>Дефицит до действия: <strong>${fmt.format(data.baseline_shortfall_tonnes)} т</strong></p>
      <p>Дефицит после действия: <strong>${fmt.format(data.scenario_shortfall_tonnes)} т</strong></p>
      <p>Сокращение дефицита: <strong>${fmt.format(data.shortfall_reduction_tonnes)} т</strong></p>
      <p class="hint">${escapeHtml(data.warning)} Источник: ${escapeHtml(data.assumption_source)}</p>`;
  } catch (error) {
    result.textContent = error.message;
  }
});

function escapeHtml(value) {
  const div = document.createElement('div');
  div.textContent = value;
  return div.innerHTML;
}

loadConfig();

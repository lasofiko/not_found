const form = document.querySelector('#capacity-form');
const result = document.querySelector('#result');
const submit = form.querySelector('button[type="submit"]');
const fmt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
let apiBase;
let activeRequest;
let requestVersion = 0;
const requestTimeoutMs = 15000;

async function loadConfig() {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), requestTimeoutMs);
  try {
    const response = await fetch(new URL('config.json', document.baseURI), { cache: 'no-store', signal: controller.signal });
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
    submit.disabled = true;
    result.textContent = 'Не удалось загрузить настройки интерфейса. Проверьте config.json и доступность сервера. ';
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.textContent = 'Повторить подключение';
    retry.addEventListener('click', loadConfig, { once: true });
    result.append(retry);
  } finally {
    window.clearTimeout(timeout);
  }
}

form.addEventListener('input', () => {
  requestVersion += 1;
  activeRequest?.abort();
  activeRequest = undefined;
  submit.disabled = !apiBase;
  result.removeAttribute('aria-busy');
  if (apiBase) result.textContent = 'Входные данные изменены. Нажмите «Рассчитать», чтобы получить новый результат.';
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!apiBase || activeRequest) return;
  const values = Object.fromEntries(new FormData(form));
  for (const key of ['demand_tonnes', 'capacity_tonnes', 'capacity_delta_tonnes']) values[key] = Number(values[key]);
  const version = ++requestVersion;
  const controller = new AbortController();
  activeRequest = controller;
  const timeout = window.setTimeout(() => controller.abort(), requestTimeoutMs);
  submit.disabled = true;
  result.setAttribute('aria-busy', 'true');
  result.textContent = 'Расчёт…';
  try {
    let response;
    try {
      response = await fetch(`${apiBase}/scenarios/capacity`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values), signal: controller.signal
      });
    } catch {
      if (controller.signal.aborted) throw new Error('Сервер не ответил за 15 секунд. Попробуйте рассчитать ещё раз.');
      throw new Error('Не удалось связаться с API. Проверьте адрес сервера и настройки CORS.');
    }
    let data;
    try {
      data = await response.json();
    } catch {
      if (controller.signal.aborted) throw new Error('Сервер не ответил за 15 секунд. Попробуйте рассчитать ещё раз.');
      throw new Error('Сервер вернул некорректный JSON-ответ.');
    }
    if (version !== requestVersion) return;
    if (!response.ok) {
      const message = data?.error?.message || 'Запрос не выполнен';
      const details = data?.error?.details?.map((item) => item.message).join('; ');
      throw new Error(`Ошибка API (${response.status}): ${message}${details ? `. ${details}` : ''}`);
    }
    const fields = ['baseline_shortfall_tonnes', 'scenario_shortfall_tonnes', 'shortfall_reduction_tonnes'];
    if (!data || data.status !== 'scenario' || data.unit !== 'tonnes' || data.period !== values.period ||
        typeof data.warning !== 'string' || typeof data.assumption_source !== 'string' ||
        fields.some(key => typeof data[key] !== 'number' || !Number.isFinite(data[key]) || data[key] < 0)) {
      throw new Error('Сервер вернул некорректный результат расчёта.');
    }
    result.innerHTML = `<h2>Результат за ${escapeHtml(data.period)}</h2>
      <p>Дефицит до действия: <strong>${fmt.format(data.baseline_shortfall_tonnes)} т</strong></p>
      <p>Дефицит после действия: <strong>${fmt.format(data.scenario_shortfall_tonnes)} т</strong></p>
      <p>Сокращение дефицита: <strong>${fmt.format(data.shortfall_reduction_tonnes)} т</strong></p>
      <p class="hint">${escapeHtml(data.warning)} Источник: ${escapeHtml(data.assumption_source)}</p>`;
  } catch (error) {
    if (version === requestVersion) result.textContent = error.message;
  } finally {
    window.clearTimeout(timeout);
    if (version === requestVersion) {
      activeRequest = undefined;
      submit.disabled = false;
      result.removeAttribute('aria-busy');
    }
  }
});

function escapeHtml(value) {
  const div = document.createElement('div');
  div.textContent = value;
  return div.innerHTML;
}

loadConfig();

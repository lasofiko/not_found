const form = document.querySelector('#capacity-form');
const result = document.querySelector('#result');
const submit = form.querySelector('button[type="submit"]');
const fmt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
let apiBase;
let activeRequest;
let generation = 0;

async function request(url, options = {}) {
  activeRequest?.abort();
  const controller = new AbortController();
  activeRequest = controller;
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal, cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error(data?.error?.message || 'Ошибка API (' + response.status + ')');
    return data;
  } catch (error) {
    if (controller.signal.aborted) throw new Error('Запрос отменён или время ожидания истекло. Повторите расчёт.');
    if (error instanceof SyntaxError) throw new Error('Сервер вернул некорректный JSON-ответ.');
    if (error instanceof TypeError) throw new Error('Не удалось связаться с API. Проверьте подключение.');
    throw error;
  } finally {
    clearTimeout(timer);
    if (activeRequest === controller) activeRequest = undefined;
  }
}
async function loadConfig() {
  submit.disabled = true;
  try {
    const config = await request(new URL('config.json', document.baseURI));
    const base = config?.apiBase;
    if (typeof base !== 'string' || !(base.startsWith('/') && !base.startsWith('//') || /^https?:\/\//i.test(base))) throw new Error('Некорректный адрес API');
    const url = new URL(base, window.location.origin);
    if (!['http:', 'https:'].includes(url.protocol) || url.search || url.hash) throw new Error('Некорректный адрес API');
    apiBase = url.href.replace(/\/+$/, '');
    submit.disabled = false;
    result.textContent = '';
  } catch {
    result.textContent = 'Не удалось загрузить настройки интерфейса. ';
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.textContent = 'Повторить подключение';
    retry.addEventListener('click', loadConfig, { once: true });
    result.append(retry);
  }
}
form.addEventListener('input', () => {
  generation += 1;
  activeRequest?.abort();
  result.textContent = 'Параметры изменены. Выполните расчёт.';
  submit.disabled = !apiBase;
});
form.addEventListener('submit', async event => {
  event.preventDefault();
  if (!apiBase) return;
  const current = ++generation;
  const values = Object.fromEntries(new FormData(form));
  for (const key of ['demand_tonnes', 'capacity_tonnes', 'capacity_delta_tonnes']) values[key] = Number(values[key]);
  result.textContent = 'Расчёт…';
  submit.disabled = true;
  try {
    const data = await request(apiBase + '/scenarios/capacity', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values) });
    if (current !== generation) return;
    const fields = ['baseline_shortfall_tonnes', 'scenario_shortfall_tonnes', 'shortfall_reduction_tonnes'];
    if (!data || data.status !== 'scenario' || data.unit !== 'tonnes' || data.period !== values.period ||
        typeof data.warning !== 'string' || typeof data.assumption_source !== 'string' ||
        fields.some(key => typeof data[key] !== 'number' || !Number.isFinite(data[key]) || data[key] < 0)) throw new Error('Сервер вернул некорректный результат расчёта.');
    result.innerHTML = '<h2>Результат за ' + escapeHtml(data.period) + '</h2>' +
      '<p>Дефицит до действия: <strong>' + fmt.format(data.baseline_shortfall_tonnes) + ' т</strong></p>' +
      '<p>Дефицит после действия: <strong>' + fmt.format(data.scenario_shortfall_tonnes) + ' т</strong></p>' +
      '<p>Сокращение дефицита: <strong>' + fmt.format(data.shortfall_reduction_tonnes) + ' т</strong></p>' +
      '<p class="hint">' + escapeHtml(data.warning) + ' Источник: ' + escapeHtml(data.assumption_source) + '</p>';
  } catch (error) {
    if (current === generation) result.textContent = error.message;
  } finally {
    if (current === generation) submit.disabled = false;
  }
});
function escapeHtml(value) {
  const div = document.createElement('div');
  div.textContent = value;
  return div.innerHTML;
}
loadConfig();

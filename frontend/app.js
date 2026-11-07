const form = document.querySelector('#capacity-form');
const result = document.querySelector('#result');
const fmt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(form));
  for (const key of ['demand_tonnes', 'capacity_tonnes', 'capacity_delta_tonnes']) values[key] = Number(values[key]);
  result.textContent = 'Расчёт…';
  try {
    const response = await fetch('/api/v1/scenarios/capacity', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values)
    });
    if (!response.ok) throw new Error(`Ошибка API: ${response.status}`);
    const data = await response.json();
    result.innerHTML = `<h2>Результат за ${escapeHtml(data.period)}</h2>
      <p>Дефицит до действия: <strong>${fmt.format(data.baseline_shortfall_tonnes)} т</strong></p>
      <p>Дефицит после действия: <strong>${fmt.format(data.scenario_shortfall_tonnes)} т</strong></p>
      <p>Сокращение дефицита: <strong>${fmt.format(data.shortfall_reduction_tonnes)} т</strong></p>
      <p class="hint">${escapeHtml(data.warning)} Источник: ${escapeHtml(data.assumption_source)}</p>`;
  } catch (error) { result.textContent = error.message; }
});
function escapeHtml(value) {
  const div = document.createElement('div'); div.textContent = value; return div.innerHTML;
}

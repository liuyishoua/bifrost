import { escapeHTML as e, metric } from "./utils.mjs";

export function lineChart(points, series, id, description) {
  const left = 40, right = 625, top = 20, bottom = 175;
  const max = Math.ceil(Math.max(2, ...points.flatMap(p => series.map(s => p[s.key]))) / 2) * 2;
  const x = i => left + i * (right - left) / Math.max(1, points.length - 1);
  const y = n => bottom - n / max * (bottom - top);
  const coords = key => points.map((p, i) => `${x(i)},${y(p[key])}`);
  const main = coords(series[0].key);
  return `<div class="interactive-chart" data-chart-id="${e(id)}"><svg class="history-curve chart" viewBox="0 0 650 210" role="group" aria-label="${e(description)}"><defs><linearGradient id="${e(id)}-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${series[0].color}" stop-opacity=".3"/><stop offset="1" stop-color="${series[0].color}" stop-opacity="0"/></linearGradient></defs>${[0, .5, 1].map(n => `<line x1="${left}" x2="${right}" y1="${y(max*n)}" y2="${y(max*n)}"/><text x="4" y="${y(max*n)+4}">${max*n}</text>`).join("")}<path d="M${left},${bottom} L${main.join(" L")} L${x(points.length-1)},${bottom} Z" fill="url(#${e(id)}-fill)"/>${series.map(s => `<polyline points="${coords(s.key).join(" ")}" fill="none" stroke="${s.color}" stroke-width="2.5" stroke-linejoin="round"/>${points.map((p,i) => `<circle cx="${x(i)}" cy="${y(p[s.key])}" r="3" fill="${s.color}"/>`).join("")}`).join("")}<g class="line-marker" visibility="hidden"><line y1="${top}" y2="${bottom}"/>${series.map(s => `<circle r="5" fill="${s.color}" stroke="#ecf8ff" stroke-width="2"/>`).join("")}</g>${points.map((p,i) => {
    const begin = i ? (x(i-1)+x(i))/2 : left;
    const end = i < points.length-1 ? (x(i)+x(i+1))/2 : right;
    const tooltip = `${p.label}\n${series.map(s => `${s.label}：${metric(p[s.key])}${s.unit || ""}`).join("\n")}`;
    return `<rect class="chart-hit" tabindex="0" x="${begin}" y="${top}" width="${end-begin}" height="${bottom-top}" fill="transparent" data-x="${x(i)}" data-ys="${e(JSON.stringify(series.map(s => y(p[s.key]))))}" data-label="${e(p.label)}" data-tooltip="${e(tooltip)}" aria-label="${e(tooltip)}"/>`;
  }).join("")}${[...new Set([0,Math.floor((points.length-1)/2),points.length-1])].map(i => `<text x="${x(i)}" y="202" text-anchor="${i===0 ? "start" : i===points.length-1 ? "end" : "middle"}">${e(points[i].tick)}</text>`).join("")}</svg><div class="line-tooltip" hidden></div></div>`;
}

export function bindLineChart(root, previous = null) {
  const tooltip = root.querySelector(".line-tooltip");
  const marker = root.querySelector(".line-marker");
  const show = hit => {
    if (!hit) return;
    const x = Number(hit.dataset.x);
    root.dataset.activeLabel = hit.dataset.label;
    tooltip.textContent = hit.dataset.tooltip;
    tooltip.hidden = false;
    const position = x / 650 * root.clientWidth;
    tooltip.style.left = `${Math.max(8, Math.min(position-tooltip.offsetWidth/2, root.clientWidth-tooltip.offsetWidth-8))}px`;
    marker.setAttribute("visibility", "visible");
    const line = marker.querySelector("line");
    line.setAttribute("x1", x); line.setAttribute("x2", x);
    const ys = JSON.parse(hit.dataset.ys);
    marker.querySelectorAll("circle").forEach((circle, i) => {
      circle.setAttribute("cx", x); circle.setAttribute("cy", ys[i]);
    });
  };
  const hide = () => {
    tooltip.hidden = true;
    marker.setAttribute("visibility", "hidden");
    delete root.dataset.activeLabel;
  };
  const hit = event => event.target.closest(".chart-hit");
  root.onpointerover = root.onpointermove = event => hit(event) ? show(hit(event)) : hide();
  root.onpointerleave = event => { if (event.pointerType !== "touch") hide(); };
  root.addEventListener("focusin", event => show(hit(event)));
  root.addEventListener("focusout", event => { if (!root.contains(event.relatedTarget)) hide(); });
  root.onclick = event => {
    const target = hit(event);
    if (target) { target.focus({preventScroll: true}); show(target); }
  };
  root.onkeydown = event => { if (event.key === "Escape") hide(); };
  if (previous?.label) {
    const target = [...root.querySelectorAll(".chart-hit")].find(node => node.dataset.label === previous.label);
    if (target) {
      if (previous.focused) target.focus({preventScroll: true});
      show(target);
    }
  }
}

/* =========================
   Configuration / sample data
   ========================= */
const CFG = {
  days: ["Mon","Tue","Wed","Thu","Fri"],
  workStartHour: 8,  // inclusive
  workEndHour: 20,   // exclusive
  canvasHeightPx: parseInt(getComputedStyle(document.documentElement).getPropertyValue('--canvas-height')) || 720,
  dayWidth: parseInt(getComputedStyle(document.documentElement).getPropertyValue('--day-width')) || 120,
  samplePeople: [
    { id: "p1", name:"Aisha", color:"#FF8A80" },
    { id: "p2", name:"Ben", color:"#FFD180" },
    { id: "p3", name:"Carmen", color:"#FFFF8D" },
    { id: "p4", name:"Diego", color:"#B9F6CA" },
    { id: "p5", name:"Eve", color:"#80D8FF" },
    { id: "p6", name:"Farah", color:"#B388FF" },
    { id: "p7", name:"Gus", color:"#CFD8DC" }
  ],
  // sample events: day index (0=Mon), start "HH:MM", end "HH:MM", personId
  events: [
    { day:0, start:"08:00", end:"09:50", person:"p1" }, // Mon
    { day:0, start:"09:30", end:"11:00", person:"p2" },
    { day:0, start:"10:45", end:"12:15", person:"p3" },
    { day:0, start:"11:30", end:"13:00", person:"p4" },
    { day:0, start:"15:00", end:"16:30", person:"p5" },
    { day:1, start:"08:30", end:"10:10", person:"p6" },
    { day:1, start:"09:00", end:"12:00", person:"p1" },
    { day:1, start:"11:50", end:"13:20", person:"p2" },
    { day:2, start:"08:00", end:"09:00", person:"p7" },
    { day:2, start:"09:15", end:"10:45", person:"p3" },
    { day:2, start:"10:00", end:"11:00", person:"p4" },
    { day:3, start:"12:00", end:"14:00", person:"p5" },
    { day:3, start:"13:30", end:"15:00", person:"p6" },
    { day:4, start:"08:00", end:"11:30", person:"p1" },
    { day:4, start:"10:00", end:"12:00", person:"p2" },
    { day:4, start:"11:20", end:"13:30", person:"p3" },
  ]
};

/* =========================
   Helpers
   ========================= */
function hhmmToMinutes(hhmm){
  const [h,m] = hhmm.split(":").map(Number);
  return h*60 + m;
}
function minutesToHHMM(min){
  const h = Math.floor(min/60);
  const m = min%60;
  const period = h >= 12 ? 'PM' : 'AM';
  const hour12 = h % 12 || 12;
  return `${hour12}:${String(m).padStart(2,'0')} ${period}`;
}

/* =========================
   Build DOM for week
   ========================= */
const daysEl = document.getElementById('days');
const timeColumnEl = document.getElementById('time-column');
CFG.days.forEach((d, idx) => {
  const dayEl = document.createElement('div');
  dayEl.className = 'day';
  dayEl.setAttribute('data-day', idx);
  dayEl.innerHTML = `
    <div class="day-label">${d}</div>
    <canvas id="canvas-${idx}" width="${CFG.dayWidth}" height="${CFG.canvasHeightPx}" aria-label="${d} availability canvas"></canvas>
  `;
  daysEl.appendChild(dayEl);
});

// Add time labels to time column
for(let hour = CFG.workStartHour; hour <= CFG.workEndHour; hour++){
  const timeEl = document.createElement('div');
  const period = hour >= 12 ? 'PM' : 'AM';
  const hour12 = hour % 12 || 12;
  timeEl.textContent = `${hour12} ${period}`;
  timeEl.style.fontSize = '12px';
  timeEl.style.color = 'var(--muted)';
  timeEl.style.textAlign = 'right';
  timeEl.style.paddingRight = '8px';
  timeEl.style.position = 'absolute';
  const y = 32 + (hour - CFG.workStartHour) * 60;
  timeEl.style.top = y + 'px';
  timeColumnEl.appendChild(timeEl);
}

/* Map people by id for easy lookup */
const peopleById = {};
CFG.samplePeople.forEach(p => peopleById[p.id] = p);

/* Preprocess events into arrays by day for faster lookup */
const eventsByDay = {};
for(let i=0;i<CFG.days.length;i++) eventsByDay[i]=[];
CFG.events.forEach(ev => {
  if (eventsByDay[ev.day]) eventsByDay[ev.day].push({
    startMin: hhmmToMinutes(ev.start),
    endMin: hhmmToMinutes(ev.end),
    person: ev.person
  });
});

/* Canvas draw: for each day draw a smooth vertical gradient
   where intensity = number of concurrent events (people busy)
*/
function drawDayGradient(dayIndex){
  const canvas = document.getElementById(`canvas-${dayIndex}`);
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  // Create an offscreen ImageData to paint per-pixel
  const image = ctx.createImageData(w, h);

  // Workday bounds in minutes
  const startMin = CFG.workStartHour * 60;
  const endMin = CFG.workEndHour * 60;
  const totalMins = endMin - startMin;

  // For each vertical pixel row, compute how many people are busy at that minute
  // We'll sample by minute => map y to minute
  // Precompute a count per minute for the day
  const counts = new Uint8Array(totalMins); // up to 255 people — enough
  const perMinutePeople = Array.from({length: totalMins}, () => []); // who is busy that minute

  const dayEvents = eventsByDay[dayIndex] || [];
  for(const e of dayEvents){
    // Clip to workday
    const s = Math.max(e.startMin, startMin);
    const t = Math.min(e.endMin, endMin);
    for(let m = s; m < t; m++){
      const idx = m - startMin;
      counts[idx] = counts[idx] + 1;
      perMinutePeople[idx].push(e.person);
    }
  }

  // Determine max concurrent count for color scale
  const maxCount = Math.max(1, ...counts);

  // Color mapping: we'll map count 0..maxCount to a color ramp (light green -> deep green)
  // Function returns [r,g,b,a]
  function colorForCount(c){
    if (c === 0) return [240,255,240,40]; // light green
    // Interpolate between light and deep
    // t from 0..1
    const t = Math.min(1, c / maxCount);
    // base RGB anchors
    const r = Math.round(240 + (0 - 240) * t);
    const g = Math.round(255 + (100 - 255) * t);
    const b = Math.round(240 + (0 - 240) * t);
    // Add alpha ramp so low counts are faint
    const a = Math.round(40 + (220 - 40) * t);
    return [r,g,b,a];
  }

  // Paint pixels: map y(0..h) to minute index (0..totalMins-1)
  for(let y=0; y<h; y++){
    // Fraction down the canvas (0 top to 1 bottom)
    const frac = y / (h-1);
    // minute index = round(frac * (totalMins-1))
    const minuteIdx = Math.min(totalMins-1, Math.round(frac * (totalMins-1)));
    const c = counts[minuteIdx] || 0;
    const [r,g,b,a] = colorForCount(c);
    for(let x=0; x<w; x++){
      const pxIndex = (y * w + x) * 4;
      image.data[pxIndex] = r;
      image.data[pxIndex+1] = g;
      image.data[pxIndex+2] = b;
      image.data[pxIndex+3] = a;
    }
  }

  ctx.putImageData(image, 0, 0);

  // Add subtle horizontal hour lines
  ctx.globalCompositeOperation = 'source-over';
  ctx.strokeStyle = 'rgba(255,255,255,0.04)';
  ctx.lineWidth = 1;
  for(let hour = CFG.workStartHour; hour <= CFG.workEndHour; hour++){
    const minuteIndex = (hour*60) - startMin;
    if (minuteIndex < 0 || minuteIndex > totalMins) continue;
    const y = Math.round((minuteIndex / (totalMins-1)) * (h-1));
    ctx.beginPath();
    ctx.moveTo(2, y+0.5);
    ctx.lineTo(w-2, y+0.5);
    ctx.stroke();
  }

  // Return the per-minute people array to use for hover lookups
  return perMinutePeople;
}

/* Draw all days and store per-day minute maps */
const perDayMinuteMaps = {};
for(let d=0; d<CFG.days.length; d++){
  perDayMinuteMaps[d] = drawDayGradient(d);
}

/* =========================
   Hover / tooltip behavior
   ========================= */
const tooltip = document.getElementById('tooltip');
const tooltipTime = document.getElementById('tooltip-time');
const tooltipList = document.getElementById('tooltip-list');

function showTooltip(x,y, htmlContent){
  tooltip.style.left = x + 'px';
  tooltip.style.top = y + 'px';
  tooltip.innerHTML = htmlContent;
  tooltip.style.display = 'block';
  tooltip.setAttribute('aria-hidden','false');
}
function hideTooltip(){
  tooltip.style.display = 'none';
  tooltip.setAttribute('aria-hidden','true');
}

/* Attach mousemove handlers to canvases */
for(let d=0; d<CFG.days.length; d++){
  const canvas = document.getElementById(`canvas-${d}`);
  canvas.addEventListener('mousemove', (ev) => {
    const rect = canvas.getBoundingClientRect();
    // y relative to canvas top
    const y = ev.clientY - rect.top;
    // clamp
    const clampedY = Math.max(0, Math.min(rect.height, y));
    // map y to minute
    const totalMins = (CFG.workEndHour - CFG.workStartHour) * 60;
    const frac = clampedY / rect.height;
    const minuteIndex = Math.round(frac * (totalMins-1));
    const absoluteMin = CFG.workStartHour*60 + minuteIndex;
    // build tooltip content
    const hhmm = minutesToHHMM(absoluteMin);
    const peopleIds = perDayMinuteMaps[d][minuteIndex] || [];
    // unique and preserve order
    const uniq = [...new Set(peopleIds)];
    const attendeesHtml = uniq.length ? uniq.map(pid => {
      const p = peopleById[pid] || {name: pid, color:"#888"};
      return `<div class="attendee"><span class="dot" style="background:${p.color}"></span><div>${p.name}</div></div>`;
    }).join('') : `<div class="muted">No one in class</div>`;
    const content = `<span class="time">${CFG.days[d]} • ${hhmm}</span>${attendeesHtml}`;
    // position tooltip near cursor but keep inside window
    const left = Math.min(window.innerWidth - 280, ev.clientX + 16);
    const top = Math.max(8, ev.clientY - 18);
    showTooltip(left, top, content);
  });

  canvas.addEventListener('mouseleave', () => {
    hideTooltip();
  });

  // Also support click to "pin" tooltip (optional)
  let pinned = false;
  canvas.addEventListener('click', (ev) => {
    pinned = !pinned;
    if (pinned) {
      canvas.style.outline = '2px solid rgba(255,255,255,0.06)';
      // keep tooltip visible
    } else {
      canvas.style.outline = 'none';
      hideTooltip();
    }
  });
}

/* Accessibility: keyboard focus -> show midday */
document.querySelectorAll('canvas').forEach((c, idx) => {
  c.tabIndex = 0;
  c.addEventListener('focus', () => {
    const middayMin = ((CFG.workStartHour + CFG.workEndHour) / 2) * 60;
    const minuteIndex = middayMin - (CFG.workStartHour*60);
    const absoluteMin = middayMin;
    const hhmm = minutesToHHMM(absoluteMin);
    const peopleIds = perDayMinuteMaps[idx][Math.round(minuteIndex)] || [];
    const uniq = [...new Set(peopleIds)];
    const attendeesHtml = uniq.length ? uniq.map(pid => {
      const p = peopleById[pid] || {name: pid, color:"#888"};
      return `<div class="attendee"><span class="dot" style="background:${p.color}"></span><div>${p.name}</div></div>`;
    }).join('') : `<div class="muted">No one in class</div>`;
    const rect = c.getBoundingClientRect();
    showTooltip(rect.right + 12, rect.top + 12, `<span class="time">${CFG.days[idx]} • ${hhmm}</span>${attendeesHtml}`);
  });
  c.addEventListener('blur', hideTooltip);
});

/* Expose a small imperative API to update events from server */
window.CalendarPrototype = {
  redraw(newEvents, people){
    // replace data if provided
    if (Array.isArray(people)) {
      // shallow replace peopleById
      peopleById = {}; // NOTE: if you plan to reuse this exact variable, adapt code
      people.forEach(p => peopleById[p.id] = p);
    }
    if (Array.isArray(newEvents)) {
      // rebuild eventsByDay
      for(let i=0;i<CFG.days.length;i++) eventsByDay[i]=[];
      newEvents.forEach(ev => {
        if (eventsByDay[ev.day]) eventsByDay[ev.day].push({
          startMin: hhmmToMinutes(ev.start),
          endMin: hhmmToMinutes(ev.end),
          person: ev.person
        });
      });
      // redraw canvases
      for(let d=0; d<CFG.days.length; d++){
        perDayMinuteMaps[d] = drawDayGradient(d);
      }
    }
  }
};

/* End prototype */
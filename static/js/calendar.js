/* =========================
   Configuration / sample data
   ========================= */
const CFG = {
  days: ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],
  workStartHour: 8,  // inclusive
  workEndHour: 20,   // exclusive
  canvasHeightPx: 720, // Will be updated on resize
  dayWidth: parseInt(getComputedStyle(document.documentElement).getPropertyValue('--day-width')) || 120,
  people: [],
  events: []
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
  // Handle case where h >= 24 (if end is 24/00)
  const normH = h % 24;
  const period = normH >= 12 ? 'PM' : 'AM';
  const hour12 = normH % 12 || 12;
  return `${hour12}:${String(m).padStart(2,'0')} ${period}`;
}

function formatHour(h) {
  const normH = h % 24;
  const period = normH >= 12 ? 'PM' : 'AM';
  const hour12 = normH % 12 || 12;
  return `${hour12} ${period}`;
}

/* =========================
   Build DOM for week
   ========================= */
const daysEl = document.getElementById('days');
// Initialize DOM for days
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

// Rebuild Time Column based on current start/end hours
function rebuildTimeColumn() {
  const timeColumnEl = document.getElementById('time-column');
  timeColumnEl.innerHTML = '';
  
  // Canvas offset (matches CSS/layout)
  const HEADER_OFFSET = 38;
  const durationHours = CFG.workEndHour - CFG.workStartHour;

  for(let hour = CFG.workStartHour; hour <= CFG.workEndHour; hour++){
    const timeEl = document.createElement('div');
    timeEl.textContent = formatHour(hour);
    
    // Position calculation
    const fraction = (hour - CFG.workStartHour) / durationHours;
    const topPx = HEADER_OFFSET + (fraction * CFG.canvasHeightPx);
    
    timeEl.style.position = 'absolute';
    timeEl.style.top = `${topPx}px`;
    timeEl.style.width = '100%';
    timeEl.style.textAlign = 'right';
    timeEl.style.paddingRight = '6px';
    timeEl.style.transform = 'translateY(-50%)'; 
    
    timeColumnEl.appendChild(timeEl);
  }
}

// Initial build
rebuildTimeColumn();

/* Map people by id for easy lookup */
let peopleById = {};

/* Preprocess events into arrays by day for faster lookup */
let eventsByDay = {};

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
      perMinutePeople[idx].push({ personId: e.person, event: e });
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
  ctx.strokeStyle = 'rgba(0,0,0,0.15)';
  ctx.lineWidth = 1.5;
  
  for(let hour = CFG.workStartHour; hour <= CFG.workEndHour; hour++){
    const minuteIndex = (hour*60) - startMin;
    if (minuteIndex < 0 || minuteIndex > totalMins) continue;
    const y = Math.round((minuteIndex / (totalMins-1)) * (h-1));
    
    // Draw line
    ctx.beginPath();
    ctx.moveTo(2, y+0.5);
    ctx.lineTo(w-2, y+0.5);
    ctx.stroke();
  }

  // Return the per-minute people array to use for hover lookups
  return perMinutePeople;
}

/* Draw all days and store per-day minute maps */
let perDayMinuteMaps = {};

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
    const entries = perDayMinuteMaps[d][minuteIndex] || [];
    
    // Deduplicate by personId, but keep event details
    const uniqueEntries = [];
    const seen = new Set();
    entries.forEach(entry => {
        if (!seen.has(entry.personId)) {
            uniqueEntries.push(entry);
            seen.add(entry.personId);
        }
    });

    const attendeesHtml = uniqueEntries.length ? uniqueEntries.map(entry => {
      const p = peopleById[entry.personId] || {name: 'Unknown', profile_image: null}; 
      const ev = entry.event;
      
      // Calculate time until free
      const remainingMins = ev.endMin - absoluteMin; 
      let timeUntilFree = "";
      if (remainingMins > 60) {
          const h = Math.floor(remainingMins / 60);
          const m = remainingMins % 60;
          timeUntilFree = `Free in ${h}h ${m}m`;
      } else {
          timeUntilFree = `Free in ${remainingMins}m`;
      }
      
      const avatarStyle = p.profile_image ? `background-image: url('/static/profile_pics/${p.profile_image}'); background-size: cover;` : `background-color: #cbd5e1; display: flex; align-items: center; justify-content: center; color: #fff; font-size: 10px; font-weight: 700;`;
      const avatarContent = p.profile_image ? '' : (p.name ? p.name.substring(0,2).toUpperCase() : '??');

      return `
      <div class="tooltip-row" style="display: flex; align-items: center; gap: 12px; margin-bottom: 12px;">
          <div class="tooltip-avatar" style="width: 36px; height: 36px; flex-shrink: 0; border-radius: 50%; ${avatarStyle}">${avatarContent}</div>
          <div class="tooltip-info" style="flex: 1; min-width: 0;">
              <div class="tooltip-name" style="font-weight: 600; font-size: 14px; color: #1e293b; line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${p.name}</div>
              <div class="tooltip-status" style="font-size: 12px; color: #64748b; margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${ev.title || 'Busy'} • ${timeUntilFree}</div>
           </div>
      </div>`;
    }).join('') : `<div class="muted" style="color: #94a3b8; font-size: 13px; font-style: italic;">No one in class</div>`;
    
    const content = `<div style="margin-bottom: 8px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: #94a3b8; font-weight: 700;">${CFG.days[d]} • ${hhmm}</div>${attendeesHtml}`;
    
    // position tooltip near cursor but keep inside window
    const left = Math.min(window.innerWidth - 300, ev.clientX + 16);
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
    const entries = perDayMinuteMaps[idx][Math.round(minuteIndex)] || [];
    const uniqueEntries = [];
    const seen = new Set();
    entries.forEach(entry => {
        if (!seen.has(entry.personId)) {
            uniqueEntries.push(entry);
            seen.add(entry.personId);
        }
    });

    const attendeesHtml = uniqueEntries.length ? uniqueEntries.map(entry => {
      const p = peopleById[entry.personId] || {name: 'Unknown', profile_image: null};
      const ev = entry.event;
      return `<div style="margin-bottom:4px;"><strong>${p.name}</strong>: ${ev.title || 'Busy'}</div>`;
    }).join('') : `<div class="muted">No one in class</div>`;
    
    const rect = c.getBoundingClientRect();
    showTooltip(rect.right + 12, rect.top + 12, `<span class="time">${CFG.days[idx]} • ${hhmm}</span>${attendeesHtml}`);
  });
  c.addEventListener('blur', hideTooltip);
});

/* Expose a small imperative API to update events from server */
function redraw(newEvents, people){
  // Update global config
  if (Array.isArray(people)) {
    CFG.people = people;
    // regenerate lookup
    peopleById = {};
    people.forEach(p => peopleById[p.id] = p);
  }
  if (Array.isArray(newEvents)) {
    CFG.events = newEvents;
    // rebuild eventsByDay
    for(let i=0;i<CFG.days.length;i++) eventsByDay[i]=[];
    newEvents.forEach(ev => {
      // Safety check for ev structure if needed, or assume valid
      if (eventsByDay[ev.day]) eventsByDay[ev.day].push({
        startMin: hhmmToMinutes(ev.start),
        endMin: hhmmToMinutes(ev.end),
        person: ev.person,
        title: ev.title
      });
    });
    // redraw canvases
    for(let d=0; d<CFG.days.length; d++){
      perDayMinuteMaps[d] = drawDayGradient(d);
    }
  }
}

function init() {
  rebuildTimeColumn();
  // Force a redraw of all days
  for(let d=0; d<CFG.days.length; d++){
    perDayMinuteMaps[d] = drawDayGradient(d);
  }
}

/* End prototype */

/* =========================
   Date Navigation & Weekend Logic
   ========================= */
(function() {
  const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  let currentMonday = getMonday(new Date());

  function getMonday(d) {
    d = new Date(d);
    var day = d.getDay(),
        diff = d.getDate() - day + (day == 0 ? -6 : 1);
    return new Date(d.setDate(diff));
  }

  function formatDate(date) {
    // e.g., "Jan 22"
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }

  function updateView() {
    const showWeekends = document.getElementById('weekend-toggle')?.checked || false;
    const numDays = showWeekends ? 7 : 5;

    // Calculate dates for the week (Mon-Sun)
    const weekDates = [];
    for(let i=0; i<7; i++) {
        const d = new Date(currentMonday);
        d.setDate(currentMonday.getDate() + i);
        weekDates.push(d);
    }
    
    // Update Header Range
    const startStr = formatDate(weekDates[0]);
    // End date depends on view mode (Friday vs Sunday)
    const endStr = formatDate(weekDates[numDays - 1]);
    const year = weekDates[0].getFullYear();
    
    const rangeDisplay = document.getElementById('date-range-display');
    if (rangeDisplay) {
        rangeDisplay.textContent = `${startStr} – ${endStr}, ${year}`;
    }

    // Update Day Labels (DOM) and CFG.days (for Tooltips)
    const dayLabelEls = document.querySelectorAll('.day-label');
    const todayStr = new Date().toDateString();
    
    weekDates.forEach((date, i) => {
        const dayName = DAY_NAMES[i];
        const dateNum = date.getDate();
        const fullLabel = `${dayName} ${dateNum}`;
        
        // Update global config so tooltip displays correct date
        // Note: we only update the array if we are within bounds
        if (i < CFG.days.length) {
            CFG.days[i] = fullLabel;
        }

        // Update DOM label if element exists
        if (dayLabelEls[i]) {
            dayLabelEls[i].textContent = fullLabel;
            
            // Highlight checking
            const dayContainer = dayLabelEls[i].parentElement;
            if (date.toDateString() === todayStr) {
                 dayContainer.classList.add('is-today');
            } else {
                 dayContainer.classList.remove('is-today');
            }
        }
    });

    // Toggle container class for CSS width/hiding
    const container = document.querySelector('.container');
    if (showWeekends) {
      container.classList.add('show-weekends');
    } else {
      container.classList.remove('show-weekends');
    }
  }

  // Event Listeners
  const btnPrev = document.getElementById('btn-prev');
  const btnNext = document.getElementById('btn-next');
  const btnToday = document.getElementById('btn-today');
  const weekendToggle = document.getElementById('weekend-toggle');

  if (weekendToggle) {
    weekendToggle.addEventListener('change', updateView);
  }

  if (btnPrev && btnNext && btnToday) {
      btnPrev.addEventListener('click', () => {
          currentMonday.setDate(currentMonday.getDate() - 7);
          updateView();
      });

      btnNext.addEventListener('click', () => {
          currentMonday.setDate(currentMonday.getDate() + 7);
          updateView();
      });
      
      btnToday.addEventListener('click', () => {
          currentMonday = getMonday(new Date());
          updateView();
      });
  }

  // Initial Run
  updateView();
})();

/* =========================
   Time Range Select Logic
   ========================= */
(function() {
  const startSelect = document.getElementById('start-time');
  const endSelect = document.getElementById('end-time');
  
  if (!startSelect || !endSelect) return;

  // Options: 0 (12 AM) to 24 (12 AM next day)
  function populateSelects() {
    startSelect.innerHTML = '';
    endSelect.innerHTML = '';
    for(let h=0; h<=24; h++) {
        // e.g. "12 AM", "1 PM", "12 AM (ends)"
        // formatHour is defined in file scope
        const label = formatHour(h);
        const optS = new Option(label, h);
        const optE = new Option(label, h);
        startSelect.add(optS);
        endSelect.add(optE);
    }
    // Set initial values
    startSelect.value = CFG.workStartHour;
    endSelect.value = CFG.workEndHour;
  }

  function onTimeChange() {
    const s = parseInt(startSelect.value);
    const e = parseInt(endSelect.value);
    
    // Validate constraint: start < end
    if (s >= e) {
        // adjust to make sense
        if (this === startSelect) {
             const newEnd = Math.min(24, s + 1);
             endSelect.value = newEnd;
        }
        else {
             const newStart = Math.max(0, e - 1);
             startSelect.value = newStart;
        }
    }

    CFG.workStartHour = parseInt(startSelect.value);
    CFG.workEndHour = parseInt(endSelect.value);

    // Redraw EVERYTHING
    rebuildTimeColumn();
    // Re-draw canvases
    for(let d=0; d<CFG.days.length; d++){
        perDayMinuteMaps[d] = drawDayGradient(d);
    }
  }

  populateSelects();
  
  startSelect.addEventListener('change', onTimeChange);
  endSelect.addEventListener('change', onTimeChange);
})();

/* =========================
   Responsive Resize Logic
   ========================= */
(function() {
  function fitToContainer() {
    const canvases = document.querySelectorAll('canvas');
    if (!canvases.length) return;
    
    // Measure available height in the flex container
    // We can just look at the first canvas's clientHeight since CSS sets height: 100%
    const rect = canvases[0].getBoundingClientRect();
    const newH = Math.max(1, Math.floor(rect.height));
    const newW = Math.max(1, Math.floor(rect.width));
    
    // Only redraw if significantly changed
    if (Math.abs(newH - CFG.canvasHeightPx) > 1 || canvases[0].width !== newW) {
        CFG.canvasHeightPx = newH;
        // CFG.dayWidth = newW; // Optional: if we want to update width config

        canvases.forEach(c => {
             c.height = newH;
             c.width = newW; // Ensure internal resolution matches CSS width
        });

        // 1. Re-calculate positions of time labels
        rebuildTimeColumn();
        
        // 2. Re-paint the canvas content
        for(let d=0; d<CFG.days.length; d++){
           perDayMinuteMaps[d] = drawDayGradient(d);
        }
    }
  }

  // Debounce helper
  function debounce(func, wait) {
    let timeout;
    return function() {
      clearTimeout(timeout);
      timeout = setTimeout(() => func.apply(this, arguments), wait);
    };
  }

  // Initial fit
  // Use timeout to ensure CSS layout is applied
  setTimeout(fitToContainer, 10);
  
  // Resize listener
  window.addEventListener('resize', debounce(fitToContainer, 100));
  
  // Also hook into the weekend toggle if it changes layout width/height
  const toggle = document.getElementById('weekend-toggle');
  if(toggle) {
    toggle.addEventListener('change', () => setTimeout(fitToContainer, 350)); // wait for transition
  }
})();
/* Export API */
window.CalendarPrototype = { redraw, init };

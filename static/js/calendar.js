/* =========================
   Configuration / sample data
   ========================= */
const CFG = {
  days: ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],
  workStartHour: 8,  // inclusive
  workEndHour: 20,   // exclusive
  canvasHeightPx: 720, // Will be updated on resize
  dayWidth: parseInt(getComputedStyle(document.documentElement).getPropertyValue('--day-width')) || 120,
  mobileDayOffset: 0, // leftmost day in the 3-day mobile window (0..maxOffset)
  people: [],
  events: []
};

/* =========================
   Mobile helpers (3-day rolling window)
   ========================= */
function isMobileViewport() {
  return window.matchMedia('(max-width: 767px)').matches;
}

function getMobileMaxOffset() {
  // Sat & Sun are always in the pool now; window of 3 across 7 days → max offset 4.
  return 4;
}

function applyMobileVisibility() {
  const dayEls = document.querySelectorAll('.day');
  if (!isMobileViewport()) {
    dayEls.forEach(el => el.classList.remove('mobile-visible'));
    return;
  }

  const maxOffset = getMobileMaxOffset();
  if (CFG.mobileDayOffset < 0) CFG.mobileDayOffset = 0;
  if (CFG.mobileDayOffset > maxOffset) CFG.mobileDayOffset = maxOffset;

  const start = CFG.mobileDayOffset;
  const end = start + 2;

  dayEls.forEach((el, idx) => {
    if (idx >= start && idx <= end) {
      el.classList.add('mobile-visible');
    } else {
      el.classList.remove('mobile-visible');
    }
  });
}

window.applyMobileVisibility = applyMobileVisibility;
window.isMobileViewport = isMobileViewport;
window.getMobileMaxOffset = getMobileMaxOffset;

// Expose CFG globally for access from calendar.html
window.CFG = CFG;

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

// Measure the vertical offset of the canvas top relative to the time-column
// top. Hardcoding this breaks on mobile because the .day padding and
// .day-label margin shrink at the 767px breakpoint, so the time labels end
// up positioned below the hour lines they're supposed to align with.
function getCanvasTopOffset() {
  const isMobile = window.matchMedia('(max-width: 767px)').matches;
  const fallback = isMobile ? 30 : 38;
  const timeColumnEl = document.getElementById('time-column');
  if (!timeColumnEl) return fallback;
  // On mobile only .day.mobile-visible canvases have layout; hidden .day
  // elements return all-zero rects and would mismeasure to 0.
  const measureCanvas =
    document.querySelector('.day.mobile-visible canvas') ||
    document.querySelector('canvas');
  if (!measureCanvas) return fallback;
  const tc = timeColumnEl.getBoundingClientRect();
  const cv = measureCanvas.getBoundingClientRect();
  if (cv.height === 0) return fallback;
  const offset = cv.top - tc.top;
  return offset > 0 && offset < 200 ? offset : fallback;
}

// Rebuild Time Column based on current start/end hours
function rebuildTimeColumn() {
  const timeColumnEl = document.getElementById('time-column');
  timeColumnEl.innerHTML = '';

  const HEADER_OFFSET = getCanvasTopOffset();
  const durationHours = CFG.workEndHour - CFG.workStartHour;
  const isMobile = window.matchMedia('(max-width: 767px)').matches;

  for(let hour = CFG.workStartHour; hour <= CFG.workEndHour; hour++){
    const timeEl = document.createElement('div');
    timeEl.textContent = formatHour(hour);

    // Position calculation
    const fraction = (hour - CFG.workStartHour) / durationHours;
    const topPx = HEADER_OFFSET + (fraction * CFG.canvasHeightPx);

    timeEl.style.position = 'absolute';
    timeEl.style.top = `${topPx}px`;
    timeEl.style.width = '100%';
    // Desktop: right-aligned against the canvas with a small breathing gap.
    // Mobile: left-aligned with a tiny inset so labels hug the screen edge
    // instead of sitting in the middle of a wider gutter.
    timeEl.style.textAlign = isMobile ? 'left' : 'right';
    timeEl.style.paddingLeft = isMobile ? '2px' : '0';
    timeEl.style.paddingRight = isMobile ? '0' : '6px';
    // End hour sits on the canvas bottom edge; keep the label fully above it.
    timeEl.style.transform = hour === CFG.workEndHour ? 'translateY(-100%)' : 'translateY(-50%)';

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
   where intensity = number of distinct people busy that minute.
   The current user is blue; everyone else is green.
   All-day events are drawn in the strip above the canvas, not here.
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

  // Get visible user IDs from global state
  const visibleIds = window.VISIBLE_USER_IDS || new Set();

  // One entry per person per minute, so two overlapping items of yours stay one shade.
  const userPeople = Array.from({length: totalMins}, () => new Set());
  const otherPeople = Array.from({length: totalMins}, () => new Set());
  const perMinutePeople = Array.from({length: totalMins}, () => []); // who is busy that minute

  const dayEvents = eventsByDay[dayIndex] || [];
  for(const e of dayEvents){
    if (e.allDay) continue;
    // Only process events for visible users
    if (!visibleIds.has(e.person)) continue;
    
    // Clip to workday
    const s = Math.max(e.startMin, startMin);
    const t = Math.min(e.endMin, endMin);
    if (s >= t) continue;
    const isUser = window.CURRENT_USER_ID && e.person === window.CURRENT_USER_ID;
    
    for(let m = s; m < t; m++){
      const idx = m - startMin;
      if (idx < 0 || idx >= totalMins) continue;
      if (isUser) {
        userPeople[idx].add(e.person);
      } else {
        otherPeople[idx].add(e.person);
      }
      perMinutePeople[idx].push({ personId: e.person, event: e });
    }
  }

  const userCounts = new Uint8Array(totalMins);
  const otherCounts = new Uint8Array(totalMins);
  for(let i = 0; i < totalMins; i++) {
    userCounts[i] = userPeople[i].size;
    otherCounts[i] = otherPeople[i].size;
  }

  // Peak distinct-people count scales every minute on this day.
  const combinedCounts = new Uint8Array(totalMins);
  for(let i = 0; i < totalMins; i++) {
    combinedCounts[i] = userCounts[i] + otherCounts[i];
  }
  const maxCount = Math.max(1, ...combinedCounts);

  const darkTheme = document.documentElement.getAttribute('data-theme') === 'dark';

  // Color mapping: blue for user, green for others.
  // Free minutes stay transparent in dark mode so they match the column
  // instead of a light-green wash that reads as a gray band.
  function getColorForMinute(userC, otherC){
    if (userC === 0 && otherC === 0) {
      return darkTheme ? [15, 23, 42, 0] : [240, 255, 240, 40];
    }
    
    // Calculate combined count and intensity relative to the day's maximum
    const combinedC = userC + otherC;
    const combinedIntensity = Math.min(1, combinedC / maxCount);
    // Apply a softer easing curve for more gradual tapering
    const t = Math.pow(combinedIntensity, 0.7);
    
    if (userC > 0) {
      // User is busy - always show blue (even if others are also busy)
      const r = Math.round(240 + (30 - 240) * t);  // Light blue to deep blue
      const g = Math.round(248 + (144 - 248) * t);
      const b = Math.round(255 + (255 - 255) * t);
      const a = Math.round(40 + (180 - 40) * t);
      return [r,g,b,a];
    } else {
      // Only others busy - green gradient
      const r = Math.round(240 + (0 - 240) * t);
      const g = Math.round(255 + (100 - 255) * t);
      const b = Math.round(240 + (0 - 240) * t);
      const a = Math.round(40 + (180 - 40) * t);
      return [r,g,b,a];
    }
  }

  // Paint pixels: map y(0..h) to minute index (0..totalMins-1)
  for(let y=0; y<h; y++){
    // Fraction down the canvas (0 top to 1 bottom)
    const frac = y / (h-1);
    // minute index = round(frac * (totalMins-1))
    const minuteIdx = Math.min(totalMins-1, Math.round(frac * (totalMins-1)));
    const userC = userCounts[minuteIdx] || 0;
    const otherC = otherCounts[minuteIdx] || 0;
    const [r,g,b,a] = getColorForMinute(userC, otherC);
    for(let x=0; x<w; x++){
      const pxIndex = (y * w + x) * 4;
      image.data[pxIndex] = r;
      image.data[pxIndex+1] = g;
      image.data[pxIndex+2] = b;
      image.data[pxIndex+3] = a;
    }
  }

  ctx.putImageData(image, 0, 0);

  // Add subtle horizontal hour lines, edge to edge (fillRect avoids stroke
  // caps being clipped at x=0 and x=w).
  ctx.globalCompositeOperation = 'source-over';
  ctx.fillStyle = darkTheme ? 'rgba(255,255,255,0.14)' : 'rgba(0,0,0,0.15)';

  for(let hour = CFG.workStartHour; hour <= CFG.workEndHour; hour++){
    const minuteIndex = (hour*60) - startMin;
    if (minuteIndex < 0 || minuteIndex > totalMins) continue;
    const y = Math.round((minuteIndex / (totalMins-1)) * (h-1));
    ctx.fillRect(0, y, w, 1);
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
  tooltip.classList.remove('tooltip-mobile-strip');
}
// Expose so navigation can dismiss a pinned mobile tooltip after a swipe
window.hideCalendarTooltip = function() {
  hideTooltip();
  touchPinned = false;
  touchPinnedCanvas = null;
};

// Build the tooltip HTML for a given day and pointer position on its canvas.
// Returned `mobileBottom: true` means the call site should use bottom-strip
// positioning rather than cursor-relative.
function buildTooltipForCanvas(d, canvas, clientX, clientY) {
  const rect = canvas.getBoundingClientRect();
  const y = clientY - rect.top;
  const clampedY = Math.max(0, Math.min(rect.height, y));
  const totalMins = (CFG.workEndHour - CFG.workStartHour) * 60;
  const frac = rect.height ? clampedY / rect.height : 0;
  const minuteIndex = Math.round(frac * (totalMins - 1));
  const absoluteMin = CFG.workStartHour * 60 + minuteIndex;
  const hhmm = minutesToHHMM(absoluteMin);
  const entries = (perDayMinuteMaps[d] && perDayMinuteMaps[d][minuteIndex]) || [];

  const uniqueEntries = [];
  const seen = new Set();
  entries.forEach(entry => {
    if (!seen.has(entry.personId)) {
      uniqueEntries.push(entry);
      seen.add(entry.personId);
    }
  });

  const attendeesHtml = uniqueEntries.length ? uniqueEntries.map(entry => {
    const p = peopleById[entry.personId] || { name: 'Unknown', profile_image: null };
    const ev = entry.event;
    const remainingMins = ev.endMin - absoluteMin;
    let timeUntilFree = '';
    if (remainingMins > 60) {
      const h = Math.floor(remainingMins / 60);
      const m = remainingMins % 60;
      timeUntilFree = `Free in ${h}h ${m}m`;
    } else {
      timeUntilFree = `Free in ${remainingMins}m`;
    }
    const profilePicPath = p.profile_image ? `${encodeURIComponent(p.profile_image)}` : '';
    const profilePicUrl = profilePicPath ? `/static/profile_pics/${profilePicPath}` : '';
    const fallbackAvatarUrl = `https://ui-avatars.com/api/?name=${encodeURIComponent(p.name)}&background=random`;
    const hasValidImage = p.profile_image &&
      p.profile_image !== 'default.jpg' &&
      p.profile_image !== 'default_group.jpg';
    const avatarUrl = hasValidImage ? profilePicUrl : fallbackAvatarUrl;
    const avatarStyle = `background-image: url('${avatarUrl}'); background-size: cover; background-position: center;`;
    return `
      <div class="tooltip-row" style="display: flex; align-items: center; gap: 12px; margin-bottom: 12px;">
          <div class="tooltip-avatar" style="width: 36px; height: 36px; flex-shrink: 0; border-radius: 50%; ${avatarStyle}"></div>
          <div class="tooltip-info" style="flex: 1; min-width: 0;">
              <div class="tooltip-name" style="font-weight: 700; font-size: 15px; color: var(--tooltip-text); line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${p.name}</div>
              <div class="tooltip-status" style="font-size: 12px; color: var(--muted); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${ev.title || 'Busy'} • ${timeUntilFree}</div>
           </div>
      </div>`;
  }).join('') : `<div class="muted" style="color: var(--muted); font-size: 13px; font-style: italic;">No one in class</div>`;

  const content = `<div style="margin-bottom: 8px; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--tooltip-text); font-weight: 700; opacity: 0.8;">${CFG.days[d]} • ${hhmm}</div>${attendeesHtml}`;
  return content;
}

function positionTooltipAtCursor(clientX, clientY) {
  tooltip.classList.remove('tooltip-mobile-strip');
  tooltip.style.left = Math.min(window.innerWidth - 300, clientX + 16) + 'px';
  tooltip.style.top = Math.max(8, clientY - 18) + 'px';
  tooltip.style.right = '';
  tooltip.style.bottom = '';
  tooltip.style.transform = 'translate(12px, -12px)';
}

function positionTooltipAsMobileStrip() {
  tooltip.classList.add('tooltip-mobile-strip');
  tooltip.style.left = '12px';
  tooltip.style.right = '12px';
  tooltip.style.bottom = 'calc(env(safe-area-inset-bottom, 0px) + 16px)';
  tooltip.style.top = 'auto';
  tooltip.style.transform = 'none';
}

let touchPinned = false;
let touchPinnedCanvas = null;

/* Attach pointer handlers to canvases */
for (let d = 0; d < CFG.days.length; d++) {
  const canvas = document.getElementById(`canvas-${d}`);
  const dayIndex = d;

  canvas.addEventListener('mousemove', (ev) => {
    if (touchPinned) return; // don't fight the pinned mobile tooltip
    const content = buildTooltipForCanvas(dayIndex, canvas, ev.clientX, ev.clientY);
    positionTooltipAtCursor(ev.clientX, ev.clientY);
    tooltip.innerHTML = content;
    tooltip.style.display = 'block';
    tooltip.setAttribute('aria-hidden', 'false');
  });

  canvas.addEventListener('mouseleave', () => {
    if (touchPinned) return;
    hideTooltip();
  });

  // Touch tap detection: only show the tooltip if the finger barely moved
  // between pointerdown and pointerup. Swipes should not pop the tooltip.
  let tapStartX = 0, tapStartY = 0, tapStartT = 0;
  canvas.addEventListener('pointerdown', (ev) => {
    if (ev.pointerType !== 'touch') return;
    tapStartX = ev.clientX;
    tapStartY = ev.clientY;
    tapStartT = Date.now();
  });
  canvas.addEventListener('pointerup', (ev) => {
    if (ev.pointerType !== 'touch') return;
    const dx = ev.clientX - tapStartX;
    const dy = ev.clientY - tapStartY;
    const dt = Date.now() - tapStartT;
    if (Math.hypot(dx, dy) > 10 || dt > 500) return; // treat as swipe/long press, not tap
    const content = buildTooltipForCanvas(dayIndex, canvas, ev.clientX, ev.clientY);
    positionTooltipAsMobileStrip();
    tooltip.innerHTML = content;
    tooltip.style.display = 'block';
    tooltip.setAttribute('aria-hidden', 'false');
    touchPinned = true;
    touchPinnedCanvas = canvas;
    // Stop the document-level dismiss handler from firing on this same tap
    ev.stopPropagation();
  });
}

// Tap outside any canvas / the tooltip itself closes the touch-pinned tooltip.
document.addEventListener('pointerdown', (ev) => {
  if (!touchPinned) return;
  if (ev.target.closest && (ev.target.closest('canvas') || ev.target.closest('.tooltip'))) return;
  hideTooltip();
  touchPinned = false;
  touchPinnedCanvas = null;
});

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
        title: ev.title,
        allDay: !!ev.all_day
      });
    });
    // redraw canvases
    for(let d=0; d<CFG.days.length; d++){
      perDayMinuteMaps[d] = drawDayGradient(d);
    }
    // Update current status display
    updateCurrentStatus();
  }
}

function init() {
  rebuildTimeColumn();
  // Force a redraw of all days
  for(let d=0; d<CFG.days.length; d++){
    perDayMinuteMaps[d] = drawDayGradient(d);
  }
  // Initialize status display
  updateCurrentStatus();
}

/* End prototype */

/* =========================
   Date Navigation & Weekend Logic
   ========================= */
(function() {
  const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const MONTH_MAX_PEOPLE = 5;
  let currentMonday = getMonday(new Date());
  let currentMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1, 12);
  let displayMode = localStorage.getItem('calendarDisplayMode') === 'month' ? 'month' : 'week';
  let monthRenderToken = 0;
  window.CALENDAR_DISPLAY_MODE = displayMode;
  window.currentMonthStart = new Date(currentMonth);

  function getMonday(d) {
    d = new Date(d);
    var day = d.getDay(),
        diff = d.getDate() - day + (day == 0 ? -6 : 1);
    return new Date(d.setDate(diff));
  }

  // Convert JS getDay() (0=Sun..6=Sat) to our Mon-first index (0=Mon..6=Sun)
  function todayDayIndex() {
    const jsDay = new Date().getDay();
    return jsDay === 0 ? 6 : jsDay - 1;
  }

  // Initialize mobile day offset so today sits in the middle of the 3-day window
  (function initMobileOffset() {
    const idx = todayDayIndex();
    const max = getMobileMaxOffset();
    CFG.mobileDayOffset = Math.max(0, Math.min(idx - 1, max));
  })();

  function formatDate(date) {
    // e.g., "Jan 22"
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }

  function updateView() {
    // Store globally so time marker can check if we're viewing current week
    window.currentWeekStart = new Date(currentMonday);

    // Weekends are always shown; week is always Mon-Sun.
    const numDays = 7;

    // Calculate dates for the week (Mon-Sun)
    const weekDates = [];
    for(let i=0; i<7; i++) {
        const d = new Date(currentMonday);
        d.setDate(currentMonday.getDate() + i);
        weekDates.push(d);
    }

    // Update Header Range
    const startStr = formatDate(weekDates[0]);
    const endStr = formatDate(weekDates[numDays - 1]);
    const year = weekDates[0].getFullYear();
    
    const rangeDisplay = document.getElementById('date-range-display');
    if (rangeDisplay && displayMode === 'week') {
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

    // Apply mobile 3-day window visibility (no-op on desktop)
    applyMobileVisibility();

    // Update mobile context strip label if it exists
    if (typeof window.updateMobileContextHeader === 'function') {
      window.updateMobileContextHeader();
    }
  }

  // Format a Date as YYYY-MM-DD in LOCAL time. We avoid `toISOString()` because
  // that converts to UTC and can shift the date by one day for users east of
  // UTC, sending the wrong week_start to the API.
  function formatLocalDate(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }

  // Monotonic counter so that, if the user clicks Next/Prev rapidly, an older
  // (slower) fetch can't clobber the result of a newer one.
  let fetchToken = 0;

  // Weeks already loaded this session. A hit paints immediately and does not
  // blank the grid. Neighbor weeks are filled in the background.
  const weekCache = new Map();
  const weekLoads = new Map();
  const monthLoads = new Map();

  function mondayOffset(dateStr, deltaWeeks) {
    const d = new Date(`${dateStr}T12:00:00`);
    d.setDate(d.getDate() + deltaWeeks * 7);
    return formatLocalDate(d);
  }

  function persistWeek(dateStr, events) {
    try {
      sessionStorage.setItem('calWeek:' + dateStr, JSON.stringify(events));
    } catch (e) { /* private mode or quota */ }
  }

  function readStoredWeek(dateStr) {
    try {
      const parsed = JSON.parse(sessionStorage.getItem('calWeek:' + dateStr) || 'null');
      return Array.isArray(parsed) ? parsed : null;
    } catch (e) {
      return null;
    }
  }

  function loadWeek(dateStr) {
    if (weekCache.has(dateStr)) return Promise.resolve(weekCache.get(dateStr));
    const existing = weekLoads.get(dateStr);
    if (existing) return existing;
    const pending = fetch(`/api/events?week_start=${dateStr}`, { cache: 'no-store' })
      .then(response => {
        if (!response.ok) throw new Error(response.statusText || 'Failed to fetch events');
        return response.json();
      })
      .then(data => {
        const events = Array.isArray(data && data.events) ? data.events : [];
        weekCache.set(dateStr, events);
        persistWeek(dateStr, events);
        return events;
      })
      .finally(() => {
        weekLoads.delete(dateStr);
      });
    weekLoads.set(dateStr, pending);
    return pending;
  }

  function prefetchNeighbors(dateStr) {
    [-1, 1].forEach(delta => {
      const key = mondayOffset(dateStr, delta);
      if (weekCache.has(key) || weekLoads.has(key)) return;
      loadWeek(key).catch(() => {});
    });
  }

  function addDays(date, count) {
    const next = new Date(date);
    next.setDate(next.getDate() + count);
    return next;
  }

  function dateFromKey(key) {
    return new Date(`${key}T12:00:00`);
  }

  function seedStoredWeek(key) {
    if (weekCache.has(key)) return;
    const stored = readStoredWeek(key);
    if (stored) weekCache.set(key, stored);
  }

  function monthDates() {
    const first = new Date(currentMonth.getFullYear(), currentMonth.getMonth(), 1, 12);
    const firstCell = getMonday(first);
    return Array.from({length: 42}, (_, index) => addDays(firstCell, index));
  }

  function monthWeekKeys(dates) {
    return [0, 7, 14, 21, 28, 35].map(index => formatLocalDate(dates[index]));
  }

  function monthEventMap(keys) {
    const byDate = new Map();
    keys.forEach(key => {
      const monday = dateFromKey(key);
      (weekCache.get(key) || []).forEach(event => {
        const eventDate = addDays(monday, Number(event.day) || 0);
        const eventKey = formatLocalDate(eventDate);
        if (!byDate.has(eventKey)) byDate.set(eventKey, []);
        byDate.get(eventKey).push(event);
      });
    });
    return byDate;
  }

  function monthPeople() {
    return Array.isArray(CFG.people) ? CFG.people : [];
  }

  function monthIntervals(events, personId) {
    const startMin = CFG.workStartHour * 60;
    const endMin = CFG.workEndHour * 60;
    return events
      .filter(event => !event.all_day && event.person === personId)
      .map(event => ({
        start: Math.max(startMin, hhmmToMinutes(event.start)),
        end: Math.min(endMin, hhmmToMinutes(event.end)),
        title: event.title || 'Busy',
      }))
      .filter(interval => interval.end > interval.start);
  }

  function sameBusyPeople(a, b) {
    if (a.length !== b.length) return false;
    return a.every((id, index) => id === b[index]);
  }

  function monthDensitySegments(events, people) {
    const startMin = CFG.workStartHour * 60;
    const endMin = CFG.workEndHour * 60;
    const schedules = people.map(person => ({
      person,
      intervals: monthIntervals(events, person.id),
    }));
    const boundaries = new Set([startMin, endMin]);
    schedules.forEach(({intervals}) => intervals.forEach(interval => {
      boundaries.add(interval.start);
      boundaries.add(interval.end);
    }));
    const points = [...boundaries].sort((a, b) => a - b);
    const result = [];
    for (let index = 0; index < points.length - 1; index++) {
      const start = points[index];
      const end = points[index + 1];
      const midpoint = start + (end - start) / 2;
      const busyIds = schedules
        .filter(({intervals}) => intervals.some(interval =>
          midpoint >= interval.start && midpoint < interval.end
        ))
        .map(({person}) => person.id)
        .sort((a, b) => a - b);
      const previous = result[result.length - 1];
      if (previous && sameBusyPeople(previous.busyIds, busyIds)) {
        previous.end = end;
      } else {
        result.push({start, end, busyIds});
      }
    }
    return result;
  }

  function setMonthSegmentPosition(element, start, end) {
    const rangeStart = CFG.workStartHour * 60;
    const rangeEnd = CFG.workEndHour * 60;
    const duration = Math.max(1, rangeEnd - rangeStart);
    element.style.setProperty('--segment-start', `${((start - rangeStart) / duration) * 100}%`);
    element.style.setProperty('--segment-width', `${((end - start) / duration) * 100}%`);
  }

  function formatMonthTime(minute) {
    if (minute === 24 * 60) return '12 AM';
    const hour = Math.floor(minute / 60);
    const mins = minute % 60;
    if (!mins) return formatHour(hour);
    const period = hour >= 12 ? 'PM' : 'AM';
    return `${hour % 12 || 12}:${String(mins).padStart(2, '0')} ${period}`;
  }

  function formatMonthRange(start, end) {
    return `${formatMonthTime(start)}–${formatMonthTime(end)}`;
  }

  function makeElement(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function monthPersonColor(element, person) {
    const color = person.id === window.CURRENT_USER_ID
      ? '#60a5fa'
      : (person.color || '#94a3b8');
    element.style.setProperty('--person-color', color);
  }

  function renderMonthLegend(people) {
    const legend = document.getElementById('month-people-legend');
    if (!legend) return;
    legend.replaceChildren();
    people.slice(0, MONTH_MAX_PEOPLE).forEach(person => {
      const item = makeElement('span', 'month-person-key');
      const dot = makeElement('i', 'month-person-dot');
      monthPersonColor(dot, person);
      item.append(dot, document.createTextNode(person.name));
      legend.appendChild(item);
    });
    if (people.length > MONTH_MAX_PEOPLE) {
      legend.appendChild(makeElement('span', 'month-person-key', `+${people.length - MONTH_MAX_PEOPLE}`));
    }
  }

  function renderMonthTimeScale() {
    const scale = document.querySelector('.month-time-scale');
    if (!scale) return;
    const start = CFG.workStartHour * 60;
    const end = CFG.workEndHour * 60;
    const duration = end - start;
    const values = [start, start + duration / 3, start + (duration * 2) / 3, end];
    [...scale.children].forEach((element, index) => {
      element.textContent = formatMonthTime(Math.round(values[index]));
    });
  }

  function renderMonthCells(dates, keys) {
    const grid = document.getElementById('month-grid');
    if (!grid) return;
    const people = monthPeople();
    const visibleIds = new Set(people.map(person => person.id));
    const byDate = monthEventMap(keys);
    const todayKey = formatLocalDate(new Date());
    grid.replaceChildren();

    dates.forEach(date => {
      const key = formatLocalDate(date);
      const events = (byDate.get(key) || []).filter(event => visibleIds.has(event.person));
      const cell = makeElement('button', 'month-cell');
      cell.type = 'button';
      cell.dataset.date = key;
      cell.setAttribute('aria-label', date.toLocaleDateString('en-US', {
        weekday: 'long', month: 'long', day: 'numeric',
      }));
      if (date.getMonth() !== currentMonth.getMonth()) cell.classList.add('is-outside');
      if (key === todayKey) cell.classList.add('is-today');

      const head = makeElement('div', 'month-day-head');
      head.appendChild(makeElement('span', 'month-day-number', date.getDate()));
      const allDay = events.filter(event => event.all_day);
      if (allDay.length) {
        const copy = allDay.length === 1 ? allDay[0].title : `${allDay.length} all-day`;
        const badge = makeElement('span', 'month-all-day-count', copy);
        badge.title = allDay.map(event => event.title).join('\n');
        head.appendChild(badge);
      }
      cell.appendChild(head);

      const lanes = makeElement('div', 'month-lanes');
      const densityLane = makeElement('div', 'month-lane month-density-lane');
      densityLane.appendChild(makeElement('span', 'month-lane-label', 'Σ'));
      const densityTrack = makeElement('span', 'month-lane-track');
      monthDensitySegments(events, people).forEach(segment => {
        const block = makeElement(
          'span',
          `month-density-segment${segment.busyIds.length ? '' : ' is-free'}`
        );
        setMonthSegmentPosition(block, segment.start, segment.end);
        const busyPeople = people.filter(person => segment.busyIds.includes(person.id));
        const density = Math.round(25 + (busyPeople.length / Math.max(1, people.length)) * 70);
        block.style.setProperty('--density', `${density}%`);
        block.title = busyPeople.length
          ? `${formatMonthRange(segment.start, segment.end)}: ${busyPeople.map(person => person.name).join(', ')} busy`
          : `${formatMonthRange(segment.start, segment.end)}: everyone free`;
        densityTrack.appendChild(block);
      });
      densityLane.appendChild(densityTrack);
      lanes.appendChild(densityLane);

      people.slice(0, MONTH_MAX_PEOPLE).forEach(person => {
        const lane = makeElement('div', 'month-lane');
        const label = makeElement('span', 'month-lane-label', (person.name || '?').charAt(0).toUpperCase());
        const track = makeElement('span', 'month-lane-track');
        monthPersonColor(label, person);
        monthPersonColor(track, person);
        const intervals = monthIntervals(events, person.id);
        intervals.forEach(interval => {
          const block = makeElement('span', 'month-busy-segment');
          setMonthSegmentPosition(block, interval.start, interval.end);
          block.title = `${person.name} busy ${formatMonthRange(interval.start, interval.end)}: ${interval.title}`;
          track.appendChild(block);
        });
        track.title = intervals.length
          ? `${person.name}: ${intervals.map(interval => formatMonthRange(interval.start, interval.end)).join(', ')}`
          : `${person.name}: free ${formatMonthRange(CFG.workStartHour * 60, CFG.workEndHour * 60)}`;
        lane.append(label, track);
        lanes.appendChild(lane);
      });
      if (people.length > MONTH_MAX_PEOPLE) {
        lanes.appendChild(makeElement(
          'span', 'month-more-people', `+${people.length - MONTH_MAX_PEOPLE} people`
        ));
      }
      cell.appendChild(lanes);
      grid.appendChild(cell);
    });

    renderMonthLegend(people);
    renderMonthTimeScale();
  }

  async function loadMonthWeeks(keys) {
    let nextIndex = 0;
    async function worker() {
      while (nextIndex < keys.length) {
        const key = keys[nextIndex++];
        try {
          await loadWeek(key);
        } catch (error) {
          console.error(`Could not load calendar week ${key}:`, error);
        }
      }
    }
    await Promise.all([worker(), worker()]);
  }

  function loadMonthRange(keys) {
    const monthKey = formatLocalDate(currentMonth);
    const existing = monthLoads.get(monthKey);
    if (existing) return existing;
    const pending = fetch(`/api/month-events?month_start=${monthKey}`, {cache: 'no-store'})
      .then(response => {
        if (!response.ok) throw new Error(response.statusText || 'Failed to fetch month');
        return response.json();
      })
      .then(data => {
        const buckets = new Map(keys.map(key => [key, []]));
        (Array.isArray(data && data.events) ? data.events : []).forEach(event => {
          if (!event.date) return;
          const mondayKey = formatLocalDate(getMonday(dateFromKey(event.date)));
          if (buckets.has(mondayKey)) buckets.get(mondayKey).push(event);
        });
        buckets.forEach((events, key) => {
          weekCache.set(key, events);
          persistWeek(key, events);
        });
      })
      .finally(() => {
        monthLoads.delete(monthKey);
      });
    monthLoads.set(monthKey, pending);
    return pending;
  }

  function syncCurrentWeekFromMonth(keys) {
    const thisWeekKey = formatLocalDate(getMonday(new Date()));
    if (!keys.includes(thisWeekKey) || !weekCache.has(thisWeekKey)) return;
    window.ALL_EVENTS = weekCache.get(thisWeekKey);
    if (window.CalendarPrototype) {
      window.CalendarPrototype.redraw(window.ALL_EVENTS, monthPeople());
    }
  }

  async function renderMonth() {
    const monthView = document.getElementById('month-view');
    if (!monthView || displayMode !== 'month') return;
    const token = ++monthRenderToken;
    window.currentMonthStart = new Date(currentMonth);
    const rangeDisplay = document.getElementById('date-range-display');
    if (rangeDisplay) {
      rangeDisplay.textContent = currentMonth.toLocaleDateString('en-US', {
        month: 'long', year: 'numeric',
      });
    }
    const dates = monthDates();
    const keys = monthWeekKeys(dates);
    keys.forEach(seedStoredWeek);
    renderMonthCells(dates, keys);
    const missing = keys.filter(key => !weekCache.has(key));
    monthView.classList.toggle('is-loading', missing.length > 0);
    if (!missing.length) {
      syncCurrentWeekFromMonth(keys);
      return;
    }
    try {
      await loadMonthRange(keys);
    } catch (error) {
      console.error('Could not load month as a single range:', error);
      await loadMonthWeeks(missing);
    }
    if (token !== monthRenderToken || displayMode !== 'month') return;
    renderMonthCells(dates, keys);
    monthView.classList.remove('is-loading');
    syncCurrentWeekFromMonth(keys);
  }

  function applyDisplayMode() {
    window.CALENDAR_DISPLAY_MODE = displayMode;
    const weekView = document.getElementById('week-view');
    const monthView = document.getElementById('month-view');
    const title = document.getElementById('calendar-title');
    if (weekView) weekView.hidden = displayMode !== 'week';
    if (monthView) monthView.hidden = displayMode !== 'month';
    if (title) title.textContent = displayMode === 'month' ? 'Monthly Availability' : 'Weekly Availability';
    document.querySelectorAll('[data-calendar-view]').forEach(button => {
      const active = button.dataset.calendarView === displayMode;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    const navCopy = displayMode === 'month' ? 'month' : (isMobileViewport() ? 'day' : 'week');
    [
      [document.getElementById('btn-prev'), `Previous ${navCopy}`],
      [document.getElementById('btn-next'), `Next ${navCopy}`],
      [document.getElementById('mobile-ctx-prev'), `Previous ${navCopy}`],
      [document.getElementById('mobile-ctx-next'), `Next ${navCopy}`],
    ].forEach(([button, label]) => {
      if (!button) return;
      button.title = label;
      button.setAttribute('aria-label', label);
    });
    if (displayMode === 'month') {
      if (window.hideCalendarTooltip) window.hideCalendarTooltip();
      const marker = document.getElementById('current-time-marker');
      if (marker) marker.style.display = 'none';
      renderMonth();
    } else {
      updateView();
      requestAnimationFrame(() => {
        if (window.fitCalendarCanvases) window.fitCalendarCanvases();
      });
    }
    if (typeof window.updateMobileContextHeader === 'function') {
      window.updateMobileContextHeader();
    }
  }

  function setDisplayMode(nextMode) {
    if (nextMode !== 'week' && nextMode !== 'month') return;
    if (nextMode === displayMode) return;
    if (nextMode === 'month') {
      const centerOfWeek = addDays(currentMonday, 3);
      currentMonth = new Date(centerOfWeek.getFullYear(), centerOfWeek.getMonth(), 1, 12);
    } else {
      const today = new Date();
      const anchor = (
        today.getFullYear() === currentMonth.getFullYear() &&
        today.getMonth() === currentMonth.getMonth()
      ) ? today : currentMonth;
      currentMonday = getMonday(anchor);
      if (isMobileViewport()) {
        const jsDay = anchor.getDay();
        const dayIndex = jsDay === 0 ? 6 : jsDay - 1;
        CFG.mobileDayOffset = Math.max(0, Math.min(dayIndex - 1, getMobileMaxOffset()));
      }
    }
    displayMode = nextMode;
    localStorage.setItem('calendarDisplayMode', displayMode);
    applyDisplayMode();
    if (displayMode === 'week') fetchWeekEvents();
  }

  // Function to fetch events for the current week from the API
  async function fetchWeekEvents() {
    const dateStr = formatLocalDate(currentMonday);
    const myToken = ++fetchToken;
    const weekEl = document.querySelector('.week');

    if (weekCache.has(dateStr)) {
      window.ALL_EVENTS = weekCache.get(dateStr);
      if (typeof updateCalendar === 'function') updateCalendar();
      if (weekEl) weekEl.classList.remove('is-loading');
      prefetchNeighbors(dateStr);
      return;
    }

    // A week saved from the last visit paints immediately. The request still
    // runs, and replaces this copy when it returns. A true miss blanks first.
    const stored = readStoredWeek(dateStr);
    if (stored) {
      window.ALL_EVENTS = stored;
      if (typeof updateCalendar === 'function') updateCalendar();
    } else {
      window.ALL_EVENTS = [];
      if (typeof updateCalendar === 'function') updateCalendar();
      if (weekEl) weekEl.classList.add('is-loading');
    }

    try {
      const events = await loadWeek(dateStr);
      if (myToken !== fetchToken) return;
      window.ALL_EVENTS = events;
      if (typeof updateCalendar === 'function') updateCalendar();
      prefetchNeighbors(dateStr);
    } catch (error) {
      if (myToken === fetchToken) console.error('Error fetching week events:', error);
    } finally {
      if (myToken === fetchToken && weekEl) weekEl.classList.remove('is-loading');
    }
  }

  // Shift the visible window by ±1 day on mobile; auto-advance a full week
  // when we run off either end of the current week.
  async function shiftMobileDay(delta) {
    if (window.hideCalendarTooltip) window.hideCalendarTooltip();
    const max = getMobileMaxOffset();
    const next = CFG.mobileDayOffset + delta;
    if (next < 0) {
      currentMonday.setDate(currentMonday.getDate() - 7);
      CFG.mobileDayOffset = getMobileMaxOffset();
      updateView();
      await fetchWeekEvents();
    } else if (next > max) {
      currentMonday.setDate(currentMonday.getDate() + 7);
      CFG.mobileDayOffset = 0;
      updateView();
      await fetchWeekEvents();
    } else {
      CFG.mobileDayOffset = next;
      updateView();
    }
    if (window.updateCurrentTimeMarker) window.updateCurrentTimeMarker();
  }

  async function jumpWeek(delta) {
    if (window.hideCalendarTooltip) window.hideCalendarTooltip();
    currentMonday.setDate(currentMonday.getDate() + delta * 7);
    updateView();
    await fetchWeekEvents();
    if (window.updateCurrentTimeMarker) window.updateCurrentTimeMarker();
  }

  async function shiftMonth(delta) {
    currentMonth = new Date(
      currentMonth.getFullYear(),
      currentMonth.getMonth() + delta,
      1,
      12
    );
    await renderMonth();
    if (typeof window.updateMobileContextHeader === 'function') {
      window.updateMobileContextHeader();
    }
  }

  async function goToday() {
    if (window.hideCalendarTooltip) window.hideCalendarTooltip();
    if (displayMode === 'month') {
      const today = new Date();
      currentMonth = new Date(today.getFullYear(), today.getMonth(), 1, 12);
      await renderMonth();
      if (typeof window.updateMobileContextHeader === 'function') {
        window.updateMobileContextHeader();
      }
      return;
    }
    currentMonday = getMonday(new Date());
    if (isMobileViewport()) {
      const idx = todayDayIndex();
      const max = getMobileMaxOffset();
      CFG.mobileDayOffset = Math.max(0, Math.min(idx - 1, max));
    }
    updateView();
    await fetchWeekEvents();
    if (window.updateCurrentTimeMarker) window.updateCurrentTimeMarker();
  }

  // Expose for the mobile context strip
  window.calendarNav = {
    prev: () => displayMode === 'month'
      ? shiftMonth(-1)
      : (isMobileViewport() ? shiftMobileDay(-1) : jumpWeek(-1)),
    next: () => displayMode === 'month'
      ? shiftMonth(+1)
      : (isMobileViewport() ? shiftMobileDay(+1) : jumpWeek(+1)),
    today: goToday,
  };

  // Event Listeners
  const btnPrev = document.getElementById('btn-prev');
  const btnNext = document.getElementById('btn-next');
  const btnToday = document.getElementById('btn-today');

  if (btnPrev) btnPrev.addEventListener('click', () => window.calendarNav.prev());
  if (btnNext) btnNext.addEventListener('click', () => window.calendarNav.next());
  if (btnToday) btnToday.addEventListener('click', () => window.calendarNav.today());

  document.querySelectorAll('[data-calendar-view]').forEach(button => {
    button.addEventListener('click', () => setDisplayMode(button.dataset.calendarView));
  });

  const monthGrid = document.getElementById('month-grid');
  if (monthGrid) {
    monthGrid.addEventListener('click', event => {
      const cell = event.target.closest('.month-cell[data-date]');
      if (!cell) return;
      const selectedDate = dateFromKey(cell.dataset.date);
      currentMonday = getMonday(selectedDate);
      if (isMobileViewport()) {
        const jsDay = selectedDate.getDay();
        const dayIndex = jsDay === 0 ? 6 : jsDay - 1;
        CFG.mobileDayOffset = Math.max(0, Math.min(dayIndex - 1, getMobileMaxOffset()));
      }
      displayMode = 'week';
      localStorage.setItem('calendarDisplayMode', displayMode);
      applyDisplayMode();
      fetchWeekEvents();
    });
  }

  // Touch swipe on the days grid (mobile only)
  (function attachSwipe() {
    const daysEl = document.getElementById('days');
    if (!daysEl) return;

    let startX = 0;
    let startY = 0;
    let tracking = false;

    daysEl.addEventListener('touchstart', (e) => {
      if (!isMobileViewport() || displayMode !== 'week') return;
      if (e.touches.length !== 1) return;
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
      tracking = true;
    }, { passive: true });

    daysEl.addEventListener('touchend', (e) => {
      if (!tracking) return;
      tracking = false;
      const t = e.changedTouches[0];
      const dx = t.clientX - startX;
      const dy = t.clientY - startY;
      // Require a mostly-horizontal swipe of at least 40px
      if (Math.abs(dx) < 40 || Math.abs(dy) > Math.abs(dx)) return;
      if (dx < 0) {
        window.calendarNav.next();
      } else {
        window.calendarNav.prev();
      }
    }, { passive: true });
  })();

  // Re-apply mobile visibility when crossing the mobile breakpoint
  let wasMobile = isMobileViewport();
  window.addEventListener('resize', () => {
    const nowMobile = isMobileViewport();
    if (nowMobile !== wasMobile) {
      wasMobile = nowMobile;
      // Re-center on today when entering mobile so the user lands somewhere useful
      if (nowMobile) {
        const idx = todayDayIndex();
        const max = getMobileMaxOffset();
        CFG.mobileDayOffset = Math.max(0, Math.min(idx - 1, max));
      }
      updateView();
      // Re-render time labels: the mobile/desktop branch changes both the
      // text alignment and the canvas-top offset.
      rebuildTimeColumn();
    } else if (nowMobile) {
      applyMobileVisibility();
    }
  });

  window.CalendarMonth = {
    refresh: () => {
      if (displayMode === 'month') renderMonth();
    },
  };

  // The page only embeds the current week when the server cache is warm.
  // Month view fills its six Monday-keyed weeks through the same cache.
  updateView();
  const initialWeek = formatLocalDate(currentMonday);
  if (window.EVENTS_INCLUDED) {
    const embedded = Array.isArray(window.ALL_EVENTS) ? window.ALL_EVENTS.slice() : [];
    weekCache.set(initialWeek, embedded);
    persistWeek(initialWeek, embedded);
  }
  applyDisplayMode();
  if (displayMode === 'week' && window.EVENTS_INCLUDED) {
    prefetchNeighbors(initialWeek);
  } else if (displayMode === 'week') {
    fetchWeekEvents();
  }
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
    if (window.CalendarMonth && typeof window.CalendarMonth.refresh === 'function') {
      window.CalendarMonth.refresh();
    }
    
    // Update current time marker
    if (window.updateCurrentTimeMarker) window.updateCurrentTimeMarker();
  }

  populateSelects();
  
  startSelect.addEventListener('change', onTimeChange);
  endSelect.addEventListener('change', onTimeChange);
})();

/* =========================
   Responsive Resize Logic
   ========================= */
(function() {
  // Pick a canvas that's actually laid out (visible). On mobile, most canvases
  // are display:none and their getBoundingClientRect returns 0x0, which would
  // collapse every canvas to 1x1. Prefer one inside a .day.mobile-visible.
  function pickMeasureCanvas() {
    const visibleDay = document.querySelector('.day.mobile-visible');
    if (visibleDay) {
      const c = visibleDay.querySelector('canvas');
      if (c) return c;
    }
    return document.querySelector('canvas');
  }

  function fitToContainer() {
    const sample = pickMeasureCanvas();
    if (!sample) return;
    const canvases = document.querySelectorAll('canvas');

    const rect = sample.getBoundingClientRect();
    const newH = Math.max(1, Math.floor(rect.height));
    const newW = Math.max(1, Math.floor(rect.width));

    // Only redraw if significantly changed
    if (Math.abs(newH - CFG.canvasHeightPx) > 1 || sample.width !== newW) {
        CFG.canvasHeightPx = newH;

        canvases.forEach(c => {
             c.height = newH;
             c.width = newW;
        });

        rebuildTimeColumn();

        for(let d=0; d<CFG.days.length; d++){
           perDayMinuteMaps[d] = drawDayGradient(d);
        }

        if (window.updateCurrentTimeMarker) window.updateCurrentTimeMarker();
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

  window.fitCalendarCanvases = fitToContainer;

  // Initial fit
  // Use timeout to ensure CSS layout is applied
  setTimeout(fitToContainer, 10);
  
  // Resize listener
  window.addEventListener('resize', debounce(fitToContainer, 100));
})();
/* Update Current Status Display (writes to desktop and mobile status elements) */
function updateCurrentStatus() {
  const statusText = document.getElementById('status-text');
  const mobileStatus = document.getElementById('mobile-ctx-status');

  if (!window.CURRENT_USER_ID) return;
  if (!statusText && !mobileStatus) return;

  function setStatus(text, variant /* 'busy' | 'free' | '' */) {
    if (statusText) {
      statusText.textContent = text;
      statusText.className = 'status-text' + (variant ? ' ' + variant : '');
    }
    if (mobileStatus) {
      mobileStatus.textContent = text;
      mobileStatus.className = 'mobile-ctx-status' + (variant ? ' ' + variant : '');
    }
  }

  const now = new Date();
  const currentDay = now.getDay(); // 0 = Sunday, 1 = Monday, etc.
  const currentMinute = now.getHours() * 60 + now.getMinutes();

  // Convert Sunday (0) to index 6, Monday (1) to 0, etc.
  const dayIndex = currentDay === 0 ? 6 : currentDay - 1;

  const todayEvents = (eventsByDay[dayIndex] || []).filter(e =>
    e.person === window.CURRENT_USER_ID
  );
  const allDayEvent = todayEvents.find(e => e.allDay) || null;
  const timedEvents = todayEvents.filter(e => !e.allDay);

  if (todayEvents.length === 0) {
    setStatus('Free for the day!', 'free');
    return;
  }

  let currentEvent = null;
  let nextEvent = null;

  for (const event of timedEvents) {
    if (currentMinute >= event.startMin && currentMinute < event.endMin) {
      currentEvent = event;
      break;
    } else if (currentMinute < event.startMin) {
      if (!nextEvent || event.startMin < nextEvent.startMin) {
        nextEvent = event;
      }
    }
  }

  if (currentEvent) {
    const minutesLeft = currentEvent.endMin - currentMinute;
    const hours = Math.floor(minutesLeft / 60);
    const mins = minutesLeft % 60;
    const timeStr = hours > 0 ? `${hours}h ${mins}m left` : `${mins}m left`;
    setStatus(`${currentEvent.title || 'Busy'} • ${timeStr}`, 'busy');
  } else if (allDayEvent) {
    setStatus(`${allDayEvent.title || 'Busy'} • all day`, 'busy');
  } else if (nextEvent) {
    const minutesUntil = nextEvent.startMin - currentMinute;
    const hours = Math.floor(minutesUntil / 60);
    const mins = minutesUntil % 60;
    const timeStr = hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;
    setStatus(`${nextEvent.title || 'Event'} in ${timeStr}`, 'free');
  } else {
    setStatus('Done for the day!', 'free');
  }
}

// Update status synchronized to the start of each minute
(function() {
  // Calculate milliseconds until next minute
  const now = new Date();
  const msUntilNextMinute = (60 - now.getSeconds()) * 1000 - now.getMilliseconds();
  
  // Schedule first update at the start of next minute
  setTimeout(() => {
    updateCurrentStatus();
    // Then update every minute on the minute
    setInterval(updateCurrentStatus, 60000);
  }, msUntilNextMinute);
})();

/* =========================
   Mobile context strip wiring + settings sheet toggle
   ========================= */
(function() {
  const btnPrev = document.getElementById('mobile-ctx-prev');
  const btnNext = document.getElementById('mobile-ctx-next');
  const btnToday = document.getElementById('mobile-ctx-today');
  const btnGear = document.getElementById('mobile-ctx-gear');
  const dateEl = document.getElementById('mobile-ctx-date');
  const sheet = document.querySelector('.right-sidebar');
  const backdrop = document.getElementById('settings-sheet-backdrop');

  if (btnPrev) btnPrev.addEventListener('click', () => window.calendarNav && window.calendarNav.prev());
  if (btnNext) btnNext.addEventListener('click', () => window.calendarNav && window.calendarNav.next());
  if (btnToday) btnToday.addEventListener('click', () => window.calendarNav && window.calendarNav.today());

  function openSheet() {
    if (!sheet) return;
    sheet.classList.add('open');
    if (backdrop) backdrop.classList.add('open');
  }
  function closeSheet() {
    if (!sheet) return;
    sheet.classList.remove('open');
    if (backdrop) backdrop.classList.remove('open');
  }
  if (btnGear) btnGear.addEventListener('click', () => {
    if (!sheet) return;
    if (sheet.classList.contains('open')) closeSheet(); else openSheet();
  });
  if (backdrop) backdrop.addEventListener('click', closeSheet);

  // Swipe-down on the sheet to close
  if (sheet) {
    let startY = 0;
    let tracking = false;
    sheet.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1) return;
      // Only treat as a close-swipe if the touch starts in the top 40px (the handle area)
      const rect = sheet.getBoundingClientRect();
      if (e.touches[0].clientY - rect.top > 40) return;
      startY = e.touches[0].clientY;
      tracking = true;
    }, { passive: true });
    sheet.addEventListener('touchend', (e) => {
      if (!tracking) return;
      tracking = false;
      const dy = e.changedTouches[0].clientY - startY;
      if (dy > 40) closeSheet();
    }, { passive: true });
  }

  // Auto-close on resize back to desktop
  window.addEventListener('resize', () => {
    if (!window.isMobileViewport || !window.isMobileViewport()) closeSheet();
  });

  // Renders the date label on the mobile context strip based on the visible 3-day window
  window.updateMobileContextHeader = function() {
    if (!dateEl) return;

    if (window.CALENDAR_DISPLAY_MODE === 'month' && window.currentMonthStart) {
      dateEl.textContent = new Date(window.currentMonthStart).toLocaleDateString('en-US', {
        month: 'long',
        year: 'numeric',
      });
      return;
    }

    const offset = (window.CFG && typeof window.CFG.mobileDayOffset === 'number') ? window.CFG.mobileDayOffset : 0;
    const start = offset;
    const end = offset + 2;

    // CFG.days already holds the formatted labels like "Mon 26" once updateView has run.
    const labels = (window.CFG && window.CFG.days) || [];
    const startLabel = labels[start] || '';
    const endLabel = labels[end] || '';

    if (window.currentWeekStart) {
      const startDate = new Date(window.currentWeekStart);
      startDate.setDate(startDate.getDate() + start);
      const endDate = new Date(window.currentWeekStart);
      endDate.setDate(endDate.getDate() + end);
      const sameMonth = startDate.getMonth() === endDate.getMonth();
      const startMonth = startDate.toLocaleDateString('en-US', { month: 'short' });
      const endMonth = endDate.toLocaleDateString('en-US', { month: 'short' });
      if (sameMonth) {
        dateEl.textContent = `${startMonth} ${startDate.getDate()} – ${endDate.getDate()}`;
      } else {
        dateEl.textContent = `${startMonth} ${startDate.getDate()} – ${endMonth} ${endDate.getDate()}`;
      }
    } else {
      dateEl.textContent = `${startLabel} – ${endLabel}`.replace(/^\s*–\s*|\s*–\s*$/g, '');
    }
  };

  // Initial paint of the mobile date label (in case updateView already ran before
  // updateMobileContextHeader was registered).
  window.updateMobileContextHeader();
})();

/* Export API */
window.CalendarPrototype = { redraw, init };

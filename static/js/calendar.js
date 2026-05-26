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
   If "Me" filter is active, use blue for user and green for others
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

  // Check if "Me" filter is active
  const showMe = document.getElementById('filter-me')?.checked || false;

  // Get visible user IDs from global state
  const visibleIds = window.VISIBLE_USER_IDS || new Set();

  // Track counts separately for user vs others when "Me" is active
  const userCounts = new Uint8Array(totalMins);
  const otherCounts = new Uint8Array(totalMins);
  const perMinutePeople = Array.from({length: totalMins}, () => []); // who is busy that minute

  const dayEvents = eventsByDay[dayIndex] || [];
  for(const e of dayEvents){
    // Only process events for visible users
    if (!visibleIds.has(e.person)) continue;
    
    // Clip to workday
    const s = Math.max(e.startMin, startMin);
    const t = Math.min(e.endMin, endMin);
    const isUser = showMe && window.CURRENT_USER_ID && e.person === window.CURRENT_USER_ID;
    
    for(let m = s; m < t; m++){
      const idx = m - startMin;
      if (isUser) {
        userCounts[idx] = userCounts[idx] + 1;
      } else {
        otherCounts[idx] = otherCounts[idx] + 1;
      }
      perMinutePeople[idx].push({ personId: e.person, event: e });
    }
  }

  // Determine max concurrent count for the entire day (combining user and others)
  // This ensures all events scale relative to the day's busiest moment
  const combinedCounts = new Uint8Array(totalMins);
  for(let i = 0; i < totalMins; i++) {
    combinedCounts[i] = userCounts[i] + otherCounts[i];
  }
  const maxCount = Math.max(1, ...combinedCounts);

  // Color mapping: blue for user, green for others
  function getColorForMinute(userC, otherC){
    if (userC === 0 && otherC === 0) return [240,255,240,40]; // very light green background
    
    // Calculate combined count and intensity relative to the day's maximum
    const combinedC = userC + otherC;
    const combinedIntensity = Math.min(1, combinedC / maxCount);
    // Apply a softer easing curve for more gradual tapering
    const t = Math.pow(combinedIntensity, 0.7);
    
    if (showMe && userC > 0) {
      // User is busy - always show blue (even if others are also busy)
      const r = Math.round(240 + (30 - 240) * t);  // Light blue to deep blue
      const g = Math.round(248 + (144 - 248) * t);
      const b = Math.round(255 + (255 - 255) * t);
      const a = Math.round(40 + (180 - 40) * t);
      return [r,g,b,a];
    } else {
      // Only others busy (or Me not active) - green gradient
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
        title: ev.title
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
  let currentMonday = getMonday(new Date());

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

  // Function to fetch events for the current week from the API
  async function fetchWeekEvents() {
    const dateStr = formatLocalDate(currentMonday);
    const myToken = ++fetchToken;

    // Immediately blank the current events so the user sees the previous week's
    // items disappear. Google Calendar fetches can take multiple seconds, and
    // without this the calendar appears stuck on the old week's events while
    // only the day labels change.
    window.ALL_EVENTS = [];
    if (typeof updateCalendar === 'function') updateCalendar();

    const weekEl = document.querySelector('.week');
    if (weekEl) weekEl.classList.add('is-loading');

    try {
      const response = await fetch(`/api/events?week_start=${dateStr}`, { cache: 'no-store' });
      if (!response.ok) {
        console.error('Failed to fetch events:', response.statusText);
        return;
      }
      const data = await response.json();

      // Ignore stale responses: a newer navigation has already started.
      if (myToken !== fetchToken) return;

      if (window.CalendarPrototype && Array.isArray(data && data.events)) {
        window.ALL_EVENTS = data.events;
        if (typeof updateCalendar === 'function') {
          updateCalendar();
        }
      }
    } catch (error) {
      console.error('Error fetching week events:', error);
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

  async function goToday() {
    if (window.hideCalendarTooltip) window.hideCalendarTooltip();
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
    prev: () => isMobileViewport() ? shiftMobileDay(-1) : jumpWeek(-1),
    next: () => isMobileViewport() ? shiftMobileDay(+1) : jumpWeek(+1),
    today: goToday,
  };

  // Event Listeners
  const btnPrev = document.getElementById('btn-prev');
  const btnNext = document.getElementById('btn-next');
  const btnToday = document.getElementById('btn-today');

  if (btnPrev) btnPrev.addEventListener('click', () => window.calendarNav.prev());
  if (btnNext) btnNext.addEventListener('click', () => window.calendarNav.next());
  if (btnToday) btnToday.addEventListener('click', () => window.calendarNav.today());

  // Touch swipe on the days grid (mobile only)
  (function attachSwipe() {
    const daysEl = document.getElementById('days');
    if (!daysEl) return;

    let startX = 0;
    let startY = 0;
    let tracking = false;

    daysEl.addEventListener('touchstart', (e) => {
      if (!isMobileViewport()) return;
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

  if (todayEvents.length === 0) {
    setStatus('Free for the day!', 'free');
    return;
  }

  let currentEvent = null;
  let nextEvent = null;

  for (const event of todayEvents) {
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

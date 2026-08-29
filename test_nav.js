// Test the actual JS execution
const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync('sweave/web/static/index.html', 'utf8');
const js = fs.readFileSync('sweave/web/static/js/app.js', 'utf8');

console.log('=== HTML Analysis ===');
console.log('Has #app:', html.includes('id="app"'));
console.log('Has nav-item buttons:', html.includes('class="nav-item"'));
console.log('Has tab-panel divs:', html.includes('class="tab-panel"'));

// Count them
const navMatches = html.match(/<button class="nav-item[^"]*"\s+data-tab="([^"]+)"/g) || [];
console.log('Nav items found:', navMatches.length);
navMatches.forEach(m => console.log('  -', m));

const panelMatches = html.match(/<div class="tab-panel[^"]*"\s+id="([^"]+)"/g) || [];
console.log('Panels found:', panelMatches.length);
panelMatches.forEach(m => console.log('  -', m));

console.log('\n=== JS Analysis ===');
console.log('Has function init:', js.includes('function init'));
console.log('Has setupEventListeners:', js.includes('function setupEventListeners'));
console.log('Has switchTab:', js.includes('function switchTab'));
console.log('Has nav-item click handler:', js.includes("$$('.nav-item').forEach"));
console.log('Has DOMContentLoaded:', js.includes("DOMContentLoaded"));

// Simulate execution
console.log('\n=== Simulated Execution ===');
const sandbox = {
  console,
  document: {
    addEventListener: (event, handler) => {
      console.log(`document.addEventListener('${event}') called`);
    },
    getElementById: (id) => {
      console.log(`getElementById('${id}') called`);
      return { classList: { add: () => {}, remove: () => {}, toggle: () => {} }, addEventListener: () => {}, querySelectorAll: () => [], appendChild: () => {} };
    },
    querySelectorAll: (sel) => {
      console.log(`querySelectorAll('${sel}') called`);
      return [];
    },
  },
  window: {
    addEventListener: () => {},
    localStorage: { getItem: () => null, setItem: () => {} },
  },
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
  WebSocket: function() { return { onopen: null, onclose: null, onmessage: null }; },
  setTimeout: (fn, ms) => { console.log(`setTimeout(${ms}ms)`); fn(); },
  console: console,
  setInterval: () => {},
  clearInterval: () => {},
};

try {
  // Try to evaluate just the event listener parts
  const initMatch = js.match(/async function init\(\)\s*{[\s\S]*?^}/m);
  if (initMatch) {
    console.log('init() body:');
    console.log(initMatch[0].substring(0, 300));
  }
} catch (e) {
  console.log('Parse error:', e.message);
}
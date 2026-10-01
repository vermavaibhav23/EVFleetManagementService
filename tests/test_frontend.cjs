const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../app/static/app.js'), 'utf8');

function harness(fetch) {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {innerHTML:'', textContent:'', disabled:false, classList:{add(){},remove(){},toggle(){}},addEventListener(){},querySelectorAll(){return[];}});
    return nodes.get(id);
  };
  const context = vm.createContext({document:{getElementById:node,querySelectorAll:()=>[],addEventListener(){}}, fetch, AbortSignal, setInterval(){},setTimeout(){}, console});
  vm.runInContext(source,context);
  return {context,node,run:s=>vm.runInContext(s,context)};
}
const overview={vehicles:0,critical_alerts:0,open_alerts:0,active_charging_plans:0,available_chargers:0,chargers:0};
function payload(url) {
  if(url.includes('/overview'))return overview;
  if(url.includes('/fleet/vehicles'))return {vehicles:[],total:0};
  if(url.includes('/simulator/status'))return {running:false,emitted_events:0};
  if(url.includes('/health'))return {status:'ready'};
  return [];
}

test('simultaneous forced refreshes coalesce into one ordered follow-up',async()=>{
  let calls=[];
  const h=harness(url=>new Promise(resolve=>calls.push(()=>resolve({ok:true,json:async()=>payload(url)}))));
  assert.equal(calls.length,8);
  const first=h.run('refresh(true)');
  const second=h.run('refresh(true)');
  assert.equal(first,second);
  calls.splice(0).forEach(resolve=>resolve());
  await new Promise(setImmediate);
  assert.equal(calls.length,8);
  calls.splice(0).forEach(resolve=>resolve());
  await first;
  assert.equal(h.node('system-label').textContent,'API connected');
  assert.equal(h.node('start-button').disabled,true);
});

test('old response cannot overwrite an action or a newer generation',async()=>{
  const pending=[];
  const h=harness(url=>new Promise(resolve=>pending.push(()=>resolve({ok:true,json:async()=>payload(url)}))));
  h.run('epoch++; busy=true');
  pending.forEach(resolve=>resolve());
  await h.run('refreshInFlight');
  assert.equal(h.run('dashboard'),null);
});

test('failed dependency is shown as reconnecting and actions are disabled',async()=>{
  const h=harness(async()=>({ok:false,status:503,json:async()=>({detail:{checks:{mongodb:'unavailable'}}})}));
  await h.run('refreshInFlight');
  assert.match(h.node('system-label').textContent,/Reconnecting/);
  assert.equal(h.node('seed-button').disabled,true);
  assert.match(h.node('message').textContent,/dependency/);
});

test('map names are escaped and cluster markers remain distinct',()=>{
  const h=harness(()=>new Promise(()=>{}));
  h.run(`drawMap(byId('fleet-map'), Array.from({length:10},(_,i)=>({lat:12.97,lon:77.59,name:'<script>bad</script>',operating_state:'PARKED',soc_pct:50})),[],[],false)`);
  const markup=h.node('fleet-map').innerHTML;
  assert.ok(!markup.includes('<script>'));
  const coords=[...markup.matchAll(/cx="([^"]+)" cy="([^"]+)"/g)].map(m=>[+m[1],+m[2]]);
  assert.equal(coords.length,10);
  for(let i=0;i<coords.length;i++)for(let j=i+1;j<coords.length;j++)assert.ok(Math.hypot(coords[i][0]-coords[j][0],coords[i][1]-coords[j][1])>=29);
});

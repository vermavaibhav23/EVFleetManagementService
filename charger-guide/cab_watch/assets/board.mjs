import {TelemetryOutbox,retryDelay} from './telemetry.mjs?v=ordered1';
import {makeCab,divert,rejoin,advance} from './motion.mjs?v=direct1';
import {cityMap,makeLayers,drawSharedRoads,selectMap,showPoint,animate} from './map.mjs?v=direct1';

const $=id=>document.getElementById(id);
const stage=c=>c.mode==='arrived'?'Completed':c.legs.length===1||c.leg===1?'With customer':c.leg===0?'To pickup':'Finishing trip';
let fleet,selected,running=false,frame,timer,changing=false,reviewGeneration=0,network,dispatchKey;
const msg=text=>{$('message').textContent=text;};
const element=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};

async function api(path,method='GET',body,extraHeaders={}){
  const token=sessionStorage.getItem('cabwatch.token');
  const headers={'Content-Type':'application/json',...extraHeaders};
  if(token && !path.startsWith('auth/'))headers.Authorization='Bearer '+token;
  const response=await fetch('/cab-watch/'+path,{method,cache:'no-store',headers,
    body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(35000)});
  if(!response.ok){
    if(response.status===401 && !path.startsWith('auth/'))signOut('Please sign in again.');
    const detail=await response.json().catch(()=>({}));
    const gap=detail.detail?.code==='sequence_gap';
    const message=gap?'Missing GPS #'+detail.detail.expected_sequence+'. Keep this tab open and retry the missing readings.':
      typeof detail.detail==='string'?detail.detail:Array.isArray(detail.detail)?detail.detail[0].msg:'Request failed ('+response.status+'). Try again.';
    const error=Error(message);error.status=response.status;
    error.retryAfter=Number(response.headers.get('Retry-After')||0)*1000;
    throw error;
  }
  return response.json();
}
function chosen(){return fleet?.cabs.find(c=>c.id===selected);}
function select(id){selected=id;selectMap(fleet.cabs,id);render();}
function alertFor(c){return fleet.alerts.find(a=>a.trip_id===c.id&&a.closed_sequence===null);}


function render(){
  if(!fleet){
    $('create').disabled=true;$('play').disabled=true;
    $('total').textContent='—';$('open').textContent='—';$('mode').textContent='Loading trips';
    return;
  }
  $('clock').textContent=[Math.floor(fleet.seconds/60),fleet.seconds%60].map(s=>String(s).padStart(2,'0')).join(':');
  $('total').textContent=fleet.cabs.filter(c=>c.mode!=='arrived').length;
  $('open').textContent=fleet.alerts.filter(a=>a.closed_sequence===null).length;
  const moving=fleet.cabs.some(c=>c.mode!=='arrived');
  $('mode').textContent=running?'● Running':moving?'Paused':'Ready';
  $('play').textContent=running?'Ⅱ Pause':'▶ Start';
  $('play').disabled=changing||(!moving&&!fleet.outbox?.length);
  $('create').disabled=changing||!$('planner').hidden;
  for(const c of fleet.cabs){
    c.row.setAttribute('aria-pressed',String(c.id===selected));
    const alarm=alertFor(c);
    c.row.classList.toggle('alarm',Boolean(alarm));
    c.badge.textContent=alarm?'Off-route alert':c.mode==='turn requested'?'Turn queued':
      c.mode==='detour'?'Route changed':stage(c);
    c.desc.textContent=c.origin+' → '+c.destination;
  }
  const c=chosen();
  if(!c){
    $('selected').textContent='Select a cab';$('tripline').textContent='Choose pickup and drop-off to start a trip.';
    $('distance').textContent='';$('wrong').disabled=true;$('return').disabled=true;
  }else{
    $('selected').textContent=c.vehicle_id+' · '+c.origin+' → '+c.destination;
    $('tripline').textContent=c.origin+' → '+c.destination+' · '+(c.metres/1000).toFixed(2)+' km · '+stage(c);
    $('distance').textContent=Math.round(c.offset)+' m from current leg · GPS #'+c.confirmed+
      ' · Alert at '+fleet.limit+' m';
    $('wrong').disabled=changing||(c.legs.length>1&&c.leg!==1)||c.mode!=='route'||c.index+1>=c.stops.length-1;
    $('return').disabled=changing||!['detour','turn requested'].includes(c.mode);
  }
  const logKey=JSON.stringify(fleet.alerts.map(a=>[a.id,a.closed_sequence,Math.round(a.peak_metres)]));
  if(logKey!==fleet.logKey){
    fleet.logKey=logKey;const log=$('alerts');log.replaceChildren();
    if(!fleet.alerts.length)log.append(element('p','No route alerts in this simulation.','muted'));
    for(const a of fleet.alerts){
      const row=element('div',undefined,'logrow'),info=element('div');
      info.append(element('strong',a.vehicle_id+' · '+(a.closed_sequence?'Resolved':'Check trip')),
        element('small','Opened at GPS #'+a.opened_sequence+' · peak '+Math.round(a.peak_metres)+' m'));
      const b=element('button','Explain alert');b.onclick=()=>explain(a);row.append(info,b);log.append(row);
    }
  }
}
async function explain(a){
  const f=fleet,generation=++reviewGeneration,pane=$('review');
  pane.hidden=false;pane.textContent='Checking the recorded GPS evidence…';
  try{
    const r=await api('me/alerts/'+a.id+'/explain','POST');
    if(f!==fleet||generation!==reviewGeneration)return;
    pane.replaceChildren(element('strong',r.source+' · '+a.vehicle_id+' · '+r.facts.status+' at review'),
      element('p',r.summary),
      element('p','Opened at GPS #'+r.facts.opened_sequence+' · peak '+r.facts.peak_m+' m'));
    if(r.notice)pane.append(element('small',r.notice));
  }catch(e){if(f===fleet&&generation===reviewGeneration)pane.textContent=e.message;}
}
function draw(now){if(fleet&&animate(fleet.cabs,now))frame=requestAnimationFrame(draw);}
function gps(f,c){
  return {trip_id:c.id,sequence:c.sequence,leg:c.leg,captured_at:c.capturedAt,
    motion:{mode:c.mode,index:c.index,source:c.from,target:c.to,recovery:[...(c.recovery||[])]},
    lat:f.origin[0]-c.point[1]*f.metres/f.lat_scale,
    lon:f.origin[1]+c.point[0]*f.metres/f.lon_scale};
}
function applyReceipt(f,result){
  const cab=f.cabs.find(c=>c.id===result.trip_id);
  if(!cab || result.sequence<cab.confirmed)return;
  cab.confirmed=result.sequence;cab.offset=result.distance_m;
  if(result.alert){
    const alert={...result.alert,vehicle_id:cab.vehicle_id,cab:cab.cab};
    f.alerts=[alert,...f.alerts.filter(a=>a.id!==alert.id)];
  }
  if(result.completed)cab.mode='arrived';
}
async function sendPositions(f){
  if(f.working || !f.outbox.length || f.retryTimer)return;
  f.working=true;
  try{
    while(f===fleet && f.outbox.length){
      const response=await api('me/gps','POST',{readings:f.outbox.first()});
      if(f!==fleet)return;
      for(const result of response.results)applyReceipt(f,result);
      f.outbox.acknowledge();f.retryAttempts=0;
      $('connection').textContent='● Connected';render();
    }
  }catch(e){
    if(f===fleet){
      const delay=retryDelay(e,f.retryAttempts||0);
      if(delay!==null){
        f.retryAttempts=(f.retryAttempts||0)+1;
        $('connection').textContent='Retrying GPS upload';
        f.retryTimer=setTimeout(()=>{f.retryTimer=null;sendPositions(f);},delay+Math.random()*250);
      }else{
        running=false;f.failed=true;$('connection').textContent='Connection interrupted';
        msg(e.message+' Press Start to retry these positions.');
      }
    }
  }finally{f.working=false;if(f===fleet)render();}
}
function schedule(f,delay){clearTimeout(timer);timer=setTimeout(()=>tick(f),delay);}
function tick(f){
  if(f!==fleet || f.failed)return;
  // Allow movement while a request is in flight, but bound unsent work on slow networks.
  if(running && f.outbox.length<2){
    const before=f.cabs.map(c=>({...c,recovery:c.recovery?[...c.recovery]:undefined}));
    const moving=f.cabs.filter(c=>advance(c));
    if(moving.length){
      const time=new Date().toISOString();
      for(const cab of moving)cab.capturedAt=time;
      const batch=moving.map(c=>gps(f,c));
      try{f.outbox.append(batch);}
      catch(e){
        f.cabs.forEach((cab,index)=>Object.assign(cab,before[index]));
        running=false;f.failed=true;msg('Unable to save pending GPS locally. '+e.message);render();return;
      }
      for(const cab of moving)showPoint(cab,cab.point);
      cancelAnimationFrame(frame);draw(performance.now());
      f.seconds+=5;
    }else running=false;
  }
  sendPositions(f);
  render();
  if(running)schedule(f,1000);
}
function closePlanner(){
  $('planner').hidden=true;$('map').classList.remove('picking');
  for(const node of document.querySelectorAll('.junction'))node.setAttribute('tabindex','-1');
}
function updatePlanner(){
  const a=$('pickup').value,b=$('dropoff').value;
  $('launch').disabled=changing||!a||!b||a===b;
  $('pickHint').textContent=!a?'Choose a pickup on the map.':!b?'Pickup '+a+' selected. Click a drop-off to start.':
    a===b?'Choose a different drop-off.':'Ready to dispatch to '+a+' → '+b+'.';
}
function pick(name){
  if($('planner').hidden||changing)return;
  if(!$('pickup').value){$('pickup').value=name;updatePlanner();return;}
  $('dropoff').value=name;updatePlanner();
  if(!$('launch').disabled)launch();
}
function openPlanner(){
  dispatchKey=crypto.randomUUID();
  $('pickup').value='';$('dropoff').value='';$('planner').hidden=false;
  $('map').classList.add('picking');
  for(const node of document.querySelectorAll('.junction'))node.setAttribute('tabindex','0');
  updatePlanner();render();
}
function showCabInRoster(v){
  if(v.row)return;
  if(!$('roster').querySelector('.cabrow'))$('roster').replaceChildren();
  v.row=element('button',undefined,'cabrow');
  v.row.style.setProperty('--cab',cityColor(v.cab));
  const top=element('span',undefined,'rowtop'),id=element('span',undefined,'cabid');
  id.append(element('i',undefined,'swatch'),element('strong','#'+v.cab+' · '+v.vehicle_id));
  v.badge=element('span','With customer','badge');top.append(id,v.badge);
  v.desc=element('span',undefined,'desc');v.row.append(top,v.desc);
  v.row.onclick=()=>select(v.id);
  $('roster').append(v.row);
}
async function launch(){
  if(changing||$('launch').disabled)return;
  const f=fleet;changing=true;
  for(const id of ['launch','cancel','pickup','dropoff'])$(id).disabled=true;
  msg('Planning the route…');
  try{
    const trip=await api('me/trips','POST',
      {pickup:$('pickup').value,dropoff:$('dropoff').value},{'Idempotency-Key':dispatchKey});
    if(f!==fleet)return;
    const c=f.cabs.find(c=>c.id===trip.id)||restoreTrip(trip);
    drawSharedRoads(f.cabs,network);
    closePlanner();running=true;f.failed=false;select(c.id);schedule(f,1000);
    msg(c.vehicle_id+' started at '+c.origin+' for '+c.destination+'.');
  }catch(e){msg(e.message);}
  finally{
    changing=false;for(const id of ['cancel','pickup','dropoff'])$(id).disabled=false;
    updatePlanner();render();
  }
}
function restoreTrip(trip){
  const c=makeCab(trip,network);
  c.leg=Math.min(trip.phase,c.legs.length-1);
  c.stops=c.legs[c.leg].stops;c.nodes=c.legs[c.leg].path;
  if(trip.last_sequence===0 && c.stops.length===1){
    c.leg=1;c.stops=c.legs[1].stops;c.nodes=c.legs[1].path;
  }
  c.capturedAt=trip.captured_at;
  c.confirmed=c.sequence=trip.last_sequence;c.offset=trip.distance_m;
  if(trip.lat!==null)c.point=[(trip.lon-fleet.origin[1])*fleet.lon_scale/fleet.metres,
    (fleet.origin[0]-trip.lat)*fleet.lat_scale/fleet.metres];
  if(trip.motion){
    c.mode=trip.motion.mode;c.index=trip.motion.index;
    c.from=trip.motion.source;c.to=trip.motion.target;c.recovery=trip.motion.recovery;
  }
  if(trip.completed)c.mode='arrived';
  fleet.cabs.push(c);makeLayers([c],select);showPoint(c,c.point);
  showCabInRoster(c);
  return c;
}
async function setup(){
  if(changing)return;
  changing=true;
  try{
    const token=sessionStorage.getItem('cabwatch.token');
    const subject=JSON.parse(atob(token.split('.')[1].replace(/-/g,'+').replace(/_/g,'/'))).sub;
    const outbox=new TelemetryOutbox(sessionStorage,subject);
    while(outbox.length){
      await api('me/gps','POST',{readings:outbox.first()});
      outbox.acknowledge();
    }
    network=network||await api('network');
    const created=await api('me/trips');
    running=false;clearTimeout(timer);cancelAnimationFrame(frame);reviewGeneration++;selected=null;closePlanner();
    clearTimeout(fleet?.retryTimer);
    fleet={outbox,...network,...created,cabs:[],seconds:0,working:false};
    cityMap(network,pick);closePlanner();
    for(const id of ['routes','shared','trails','pins'])$(id).replaceChildren();
    for(const id of ['pickup','dropoff']){
      $(id).replaceChildren(new Option('Select node',''));
      for(const n of Object.keys(network.nodes))$(id).add(new Option(n,n));
    }
    $('roster').replaceChildren(element('p','Add a trip to see its cab.','inner muted'));
    for(const trip of created.trips)restoreTrip(trip);
    drawSharedRoads(fleet.cabs,network);
    selected=(fleet.cabs.find(c=>c.mode!=='arrived')||fleet.cabs.at(-1))?.id;
    if(selected){selectMap(fleet.cabs,selected);draw(performance.now());}
    $('alerts').textContent='No route alerts in this simulation.';$('review').hidden=true;
    $('connection').textContent='● Ready';
    msg(fleet.cabs.length?'Add a trip or press Start to resume.':'Add a trip to choose a pickup and drop-off.');
  }catch(e){
    msg(e.message+' Try again.');$('connection').textContent='Offline';
    const delay=retryDelay(e,0);
    if(delay!==null && sessionStorage.getItem('cabwatch.token')){
      clearTimeout(timer);timer=setTimeout(()=>setup(),delay+Math.random()*250);
    }
  }
  finally{changing=false;render();}
}
function cityColor(cab){return ['#2164cb','#a53685','#00796b','#be5422','#664bac','#a62844',
  '#11759c','#637521','#ab400f','#594ba6','#08805a','#ad365e','#38578c','#885a0b',
  '#426d57','#804991','#2d7782','#8c5436','#536aaf','#923b32'][(cab-1)%20];}

$('pickup').onchange=$('dropoff').onchange=updatePlanner;
$('launch').onclick=launch;$('cancel').onclick=()=>{closePlanner();render();};
$('create').onclick=openPlanner;
$('play').onclick=()=>{if(!fleet)return;running=!running;fleet.failed=false;render();
  msg(running?'Simulation running · each update advances five simulated seconds.':'Simulation paused.');schedule(fleet,0);};
$('wrong').onclick=()=>{divert(chosen());render();
  msg(running?'Turn queued for the next junction.':'Turn queued for the next junction. Press Start to drive.');};
$('return').onclick=()=>{rejoin(chosen());render();
  msg(running?'Cab is rejoining its current planned leg.':'Return requested. Press Start to drive.');};
let registering=false;
function signOut(message=''){
  sessionStorage.removeItem('cabwatch.token');sessionStorage.removeItem('cabwatch.name');
  clearTimeout(fleet?.retryTimer);fleet=null;running=false;selected=null;clearTimeout(timer);cancelAnimationFrame(frame);
  $('dashboard').hidden=true;$('account').hidden=false;$('authMessage').textContent=message;
  $('connection').textContent='Signed out';$('password').value='';
}
async function openDashboard(){
  $('signedName').textContent=sessionStorage.getItem('cabwatch.name')||'My fleet';
  $('account').hidden=true;$('dashboard').hidden=false;await setup();
}
$('switchAuth').onclick=()=>{
  registering=!registering;
  $('authTitle').textContent=$('authSubmit').textContent=registering?'Create account':'Sign in';
  $('switchAuth').textContent=registering?'Use an existing account':'Create an account';
  $('nameLabel').hidden=!registering;$('accountName').required=registering;
  $('password').autocomplete=registering?'new-password':'current-password';
  $('authMessage').textContent='';
};
$('authForm').onsubmit=async event=>{
  event.preventDefault();$('authSubmit').disabled=true;
  try{
    const body={email:$('email').value,password:$('password').value};
    if(registering)body.name=$('accountName').value;
    const account=await api(registering?'auth/register':'auth/login','POST',body);
    sessionStorage.setItem('cabwatch.token',account.access_token);
    sessionStorage.setItem('cabwatch.name',account.name);$('password').value='';
    await openDashboard();
  }catch(e){$('authMessage').textContent=e.message;}
  finally{$('authSubmit').disabled=false;}
};
$('logout').onclick=()=>{signOut();location.reload();};
if(sessionStorage.getItem('cabwatch.token'))openDashboard();
else signOut();


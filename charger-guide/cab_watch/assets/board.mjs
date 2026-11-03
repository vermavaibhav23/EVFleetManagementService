import {makeCab,divert,rejoin,advance} from './motion.mjs?v=junction5';
import {cityMap,drawDepots,makeLayers,drawSharedRoads,selectMap,showPoint,animate} from './map.mjs?v=passenger4';

const $=id=>document.getElementById(id);
const STAGES=['To pickup','With customer','Returning to depot'];
let fleet,selected,running=false,frame,timer,changing=false,reviewGeneration=0,network,draftId;
const msg=text=>{$('message').textContent=text;};
const element=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};

async function api(path,method='GET',body){
  const response=await fetch('/cab-watch/'+path,{method,cache:'no-store',headers:{'Content-Type':'application/json'},
    body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(35000)});
  if(!response.ok){
    const detail=await response.json().catch(()=>({}));
    throw Error(typeof detail.detail==='string'?detail.detail:'Request failed ('+response.status+'). Try again.');
  }
  return response.json();
}
function chosen(){return fleet?.cabs.find(c=>c.id===selected);}
function select(id){selected=id;selectMap(fleet.cabs,id);render();}
function alertFor(c){return fleet.alerts.find(a=>a.trip_id===c.id&&a.closed_sequence===null);}
function depotFor(v){return fleet.depots.find(d=>d.id===v.depot_id);}

function render(){
  if(!fleet){
    $('create').disabled=true;$('play').disabled=true;
    $('total').textContent='—';$('open').textContent='—';$('mode').textContent='Set up fleet';
    return;
  }
  $('clock').textContent=[Math.floor(fleet.seconds/60),fleet.seconds%60].map(s=>String(s).padStart(2,'0')).join(':');
  $('total').textContent=fleet.vehicles.length;
  $('open').textContent=fleet.alerts.filter(a=>a.closed_sequence===null).length;
  const moving=fleet.cabs.some(c=>c.mode!=='arrived');
  $('mode').textContent=running?'● Running':moving?'Paused':'Ready';
  $('play').textContent=running?'Ⅱ Pause':'▶ Start';
  $('play').disabled=changing||!moving;
  $('create').disabled=changing||!fleet.vehicles.some(v=>v.status==='available')||!$('planner').hidden;
  for(const v of fleet.vehicles){
    if(!v.row)continue;
    const trip=fleet.cabs.find(c=>c.id===v.currentTrip);
    v.row.setAttribute('aria-pressed',String(v.latestTrip===selected));
    const detouring=trip?.mode==='detour', alarm=trip && alertFor(trip);
    v.row.classList.toggle('alarm',Boolean(alarm));
    v.badge.textContent=alarm?'Off-route alert':trip?.mode==='turn requested'?'Turn queued':detouring?'Route changed':trip?.streak?'Checking':
      v.status==='available'?'Available':STAGES[trip?.leg]||'Driving';
    v.badge.className='badge'+(trip?.streak?' warn':'');
    v.desc.textContent=v.status==='available'?depotFor(v).name+' · ready':
      (trip?trip.origin+' → '+trip.destination+' · '+STAGES[trip.leg]:'Driving');
  }
  const c=chosen();
  if(!c){
    $('selected').textContent='Select a cab';$('tripline').textContent='Add a trip to dispatch the closest available car.';
    $('distance').textContent='';$('wrong').disabled=true;$('return').disabled=true;
  }else{
    $('selected').textContent='Cab '+c.cab+' · '+c.origin+' → '+c.destination;
    $('tripline').textContent=c.depot+' → pickup '+c.origin+' → drop-off '+c.destination+
      ' → '+c.arrival_depot+' · '+(c.total_metres/1000).toFixed(2)+' km · '+
      (c.mode==='arrived'?'Completed':STAGES[c.leg]+' / '+c.mode);
    $('distance').textContent=Math.round(c.offset)+' m from current leg · GPS #'+c.confirmed+
      ' · Alert at '+fleet.limit+' m';
    $('wrong').disabled=changing||c.leg!==1||c.mode!=='route'||c.index+1>=c.stops.length-1;
    $('return').disabled=changing||!['detour','turn requested'].includes(c.mode);
  }
  const logKey=JSON.stringify(fleet.alerts.map(a=>[a.id,a.closed_sequence,Math.round(a.peak_metres)]));
  if(logKey!==fleet.logKey){
    fleet.logKey=logKey;const log=$('alerts');log.replaceChildren();
    if(!fleet.alerts.length)log.append(element('p','No route alerts in this simulation.','muted'));
    for(const a of fleet.alerts){
      const row=element('div',undefined,'logrow'),info=element('div');
      info.append(element('strong','Cab '+a.cab+' · '+(a.closed_sequence?'Resolved':'Check trip')),
        element('small','Opened at GPS #'+a.opened_sequence+' · peak '+Math.round(a.peak_metres)+' m'));
      const b=element('button','Explain alert');b.onclick=()=>explain(a);row.append(info,b);log.append(row);
    }
  }
}
async function explain(a){
  const f=fleet,generation=++reviewGeneration,pane=$('review');
  pane.hidden=false;pane.textContent='Checking the recorded GPS evidence…';
  try{
    const r=await api('sessions/'+f.id+'/alerts/'+a.id+'/explain','POST');
    if(f!==fleet||generation!==reviewGeneration)return;
    pane.replaceChildren(element('strong',r.source+' · Cab '+a.cab+' · '+r.facts.status+' at review'),
      element('p',r.summary),
      element('p','Opening readings: '+r.facts.opening_samples.map(x=>'#'+x.sequence+' = '+x.metres+' m').join(' · ')));
    if(r.notice)pane.append(element('small',r.notice));
  }catch(e){if(f===fleet&&generation===reviewGeneration)pane.textContent=e.message;}
}
function draw(now){if(fleet&&animate(fleet.cabs,now))frame=requestAnimationFrame(draw);}
function gps(f,c){
  return {trip_id:c.id,sequence:c.sequence,leg:c.leg,
    lat:f.origin[0]-c.point[1]*f.metres/f.lat_scale,
    lon:f.origin[1]+c.point[0]*f.metres/f.lon_scale};
}
async function readState(f){
  const result=await api('sessions/'+f.id);
  if(fleet!==f)return;
  f.alerts=result.alerts;
  for(const saved of result.vehicles){
    const v=f.vehicles.find(v=>v.id===saved.id);
    if(!v)continue;
    v.status=saved.status;v.current_node=saved.current_node;v.depot_id=saved.depot_id;
    if(v.status==='available')v.currentTrip=null;
  }
  for(const trip of result.trips){
    const c=f.cabs.find(c=>c.id===trip.id);
    if(!c)continue;
    c.streak=trip.off_count;c.offset=trip.distance_m;c.confirmed=trip.last_sequence;
    if(trip.lat!==null)showPoint(c,[(trip.lon-f.origin[1])*f.lon_scale/f.metres,
      (f.origin[0]-trip.lat)*f.lat_scale/f.metres]);
    if(c.mode==='arrived')c.map.pin.setAttribute('visibility','hidden');
  }
  drawDepots(network,f.vehicles);
  cancelAnimationFrame(frame);draw(performance.now());render();
}
function schedule(f,delay){clearTimeout(timer);if(!f.working)timer=setTimeout(()=>tick(f),delay);}
async function tick(f){
  if(f!==fleet||f.working)return;
  f.working=true;
  try{
    if(f.waiting){
      await readState(f);
      if(f!==fleet)return;
      if(f.cabs.some(c=>c.confirmed<c.sequence))return;
      f.waiting=false;
    }
    if(!f.pending&&running){
      const moving=f.cabs.filter(c=>advance(c));
      if(!moving.length){running=false;msg('All dispatched cars returned to their depots.');return;}
      f.pending=moving.map(c=>gps(f,c));f.seconds+=5;
    }
    if(f.pending){
      await api('sessions/'+f.id+'/gps','POST',{readings:f.pending});
      if(f!==fleet)return;
      f.pending=null;f.waiting=true;
      await readState(f);
      if(f!==fleet)return;
      f.waiting=f.cabs.some(c=>c.confirmed<c.sequence);
    }
    if(f.cabs.every(c=>c.mode==='arrived')&&!f.waiting)running=false;
    $('connection').textContent='● Connected';
  }catch(e){
    if(f!==fleet)return;
    running=false;$('connection').textContent='Connection interrupted';
    msg(e.message+' Press Start to resume.');f.failed=true;
  }finally{
    f.working=false;
    if(f===fleet){render();if(!f.failed&&(running||f.waiting||f.pending))schedule(f,f.waiting?350:950);}
  }
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
  draftId=crypto.randomUUID();
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
  id.append(element('i',undefined,'swatch'),element('strong','Cab '+v.cab));
  v.badge=element('span','Available','badge');top.append(id,v.badge);
  v.desc=element('span',undefined,'desc');v.row.append(top,v.desc);
  v.row.onclick=()=>{if(v.latestTrip)select(v.latestTrip);};
  $('roster').append(v.row);
}
async function launch(){
  if(changing||$('launch').disabled)return;
  const f=fleet;changing=true;
  for(const id of ['launch','cancel','reset','pickup','dropoff','fleetSize'])$(id).disabled=true;
  msg('Finding the closest available car by road…');
  try{
    const trip=await api('sessions/'+f.id+'/trips/'+draftId,'PUT',
      {pickup:$('pickup').value,dropoff:$('dropoff').value});
    if(f!==fleet)return;
    const c=makeCab(trip,network);
    f.cabs.push(c);makeLayers([c],select);drawSharedRoads(f.cabs,network);
    const v=f.vehicles.find(v=>v.id===trip.vehicle_id);
    v.status='busy';v.currentTrip=c.id;v.latestTrip=c.id;
    showCabInRoster(v);
    drawDepots(network,f.vehicles);
    closePlanner();running=true;f.failed=false;select(c.id);schedule(f,0);
    msg('Cab '+c.cab+' left '+c.depot+' for pickup '+c.origin+'.');
  }catch(e){msg(e.message);}
  finally{
    changing=false;for(const id of ['cancel','reset','pickup','dropoff','fleetSize'])$(id).disabled=false;
    updatePlanner();render();
  }
}
async function setup(){
  if(changing||!$('fleetSize').reportValidity())return;
  changing=true;running=false;clearTimeout(timer);cancelAnimationFrame(frame);reviewGeneration++;
  fleet=null;selected=null;closePlanner();
  $('reset').disabled=true;$('fleetSize').disabled=true;
  try{
    network=network||await api('network');
    const created=await api('sessions','POST',{count:Number($('fleetSize').value)});
    fleet={...network,...created,cabs:[],seconds:0,alerts:[],working:false};
    cityMap(network,pick);closePlanner();
    for(const id of ['routes','shared','trails','pins'])$(id).replaceChildren();
    drawDepots(network,fleet.vehicles);
    for(const id of ['pickup','dropoff']){
      $(id).replaceChildren(new Option('Select node',''));
      for(const n of Object.keys(network.nodes))$(id).add(new Option(n,n));
    }
    $('roster').replaceChildren(element('p','Add a trip to see its cab.','inner muted'));
    $('alerts').textContent='No route alerts in this simulation.';$('review').hidden=true;
    $('connection').textContent='● Ready';
    msg(fleet.vehicles.length+' cars placed across '+fleet.depots.length+' depots as evenly as possible. Add a trip.');
  }catch(e){msg(e.message+' Select New fleet to retry.');$('connection').textContent='Offline';}
  finally{changing=false;$('reset').disabled=false;$('fleetSize').disabled=false;render();}
}
function cityColor(cab){return ['#2164cb','#a53685','#00796b','#be5422','#664bac','#a62844',
  '#11759c','#637521','#ab400f','#594ba6','#08805a','#ad365e','#38578c','#885a0b',
  '#426d57','#804991','#2d7782','#8c5436','#536aaf','#923b32'][(cab-1)%20];}

$('fleetSize').onkeydown=e=>{if(e.key==='Enter')setup();};
$('pickup').onchange=$('dropoff').onchange=updatePlanner;
$('launch').onclick=launch;$('cancel').onclick=()=>{closePlanner();render();};
$('create').onclick=openPlanner;$('reset').onclick=setup;
$('play').onclick=()=>{if(!fleet)return;running=!running;fleet.failed=false;render();
  msg(running?'Simulation running · each update advances five simulated seconds.':'Simulation paused.');schedule(fleet,0);};
$('wrong').onclick=()=>{divert(chosen());render();
  msg(running?'Turn queued for the next junction.':'Turn queued for the next junction. Press Start to drive.');};
$('return').onclick=()=>{rejoin(chosen());render();
  msg(running?'Cab is rejoining its current planned leg.':'Return requested. Press Start to drive.');};
msg('Enter the number of cars and select Set fleet.');


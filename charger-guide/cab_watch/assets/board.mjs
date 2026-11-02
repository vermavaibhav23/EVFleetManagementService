import {makeCab,divert,rejoin,advance} from './motion.mjs';
import {cityMap,makeLayers,selectMap,showPoint,animate} from './map.mjs';

const $=id=>document.getElementById(id);
let fleet, selected, running=false, frame, timer, changing=false, reviewGeneration=0;
const msg=text=>{$('message').textContent=text;};
const element=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
async function api(path,method='GET',body){
  const response=await fetch('/cab-watch/'+path,{method,cache:'no-store',headers:{'Content-Type':'application/json'},
    body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(35000)});
  if(!response.ok)throw Error('Request failed ('+response.status+'). Try again.');
  return response.json();
}
function chosen(){return fleet?.cabs.find(c=>c.id===selected);}
function select(id){
  selected=id;selectMap(fleet.cabs,id);render();
}
function alertFor(c){return fleet.alerts.find(a=>a.trip_id===c.id&&a.closed_sequence===null);}
function render(){
  if(!fleet)return;
  $('clock').textContent=[Math.floor(fleet.seconds/60),fleet.seconds%60].map(s=>String(s).padStart(2,'0')).join(':');
  $('total').textContent=fleet.cabs.length;$('open').textContent=fleet.alerts.filter(a=>a.closed_sequence===null).length;
  const arrived=fleet.cabs.every(c=>c.mode==='arrived');
  $('mode').textContent=running?'● Running':arrived?'Completed':'Paused';
  $('play').textContent=running?'Ⅱ Pause':arrived?'Completed':'▶ Start';$('play').disabled=changing||arrived;
  const c=chosen();
  $('selected').textContent='Cab '+c.cab+' · '+c.origin+' → '+c.destination;
  $('tripline').textContent='Driver simulation: '+c.mode+'.';
  $('distance').textContent=Math.round(c.offset)+' m from planned route · GPS #'+c.confirmed+' · '+Math.min(c.streak,3)+'/3 off-route readings';
  $('wrong').disabled=changing||c.mode!=='route';$('return').disabled=changing||!['detour','turn requested'].includes(c.mode);
  for(const v of fleet.cabs){
    v.row.setAttribute('aria-pressed',String(v.id===selected));
    v.badge.textContent=alertFor(v)?'Check trip':v.streak?'Checking':v.mode==='arrived'?'Arrived':'On route';
    v.badge.className='badge'+(v.streak?' warn':'');
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
  const f=fleet, generation=++reviewGeneration;const pane=$('review');
  pane.hidden=false;pane.textContent='Checking the recorded GPS evidence…';
  try{
    const r=await api('sessions/'+f.id+'/alerts/'+a.id+'/explain','POST');
    if(f!==fleet || generation!==reviewGeneration)return;
    pane.replaceChildren(element('strong',r.source+' · Cab '+a.cab+' · '+r.facts.status+' at review'),element('p',r.summary),
      element('p','Opening readings: '+r.facts.opening_samples.map(x=>'#'+x.sequence+' = '+x.metres+' m').join(' · ')));
    if(r.notice)pane.append(element('small',r.notice));
  }catch(e){if(f===fleet&&generation===reviewGeneration)pane.textContent=e.message;}
}
function draw(now){if(fleet&&animate(fleet.cabs,now))frame=requestAnimationFrame(draw);}
function gps(f,c){
  return {trip_id:c.id,sequence:c.sequence,lat:f.origin[0]-c.point[1]*f.metres/f.lat_scale,
    lon:f.origin[1]+c.point[0]*f.metres/f.lon_scale};
}
async function readState(f){
  const result=await api('sessions/'+f.id);
  if(fleet!==f)return;
  f.alerts=result.alerts;
  for(const trip of result.trips){
    const c=f.cabs.find(c=>c.id===trip.id);
    c.streak=trip.off_count;c.offset=trip.distance_m;c.confirmed=trip.last_sequence;
    if(trip.lat!==null)showPoint(c,[(trip.lon-f.origin[1])*f.lon_scale/f.metres,(f.origin[0]-trip.lat)*f.lat_scale/f.metres]);
  }
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
      if(!moving.length){running=false;msg('All cabs reached their destinations. Choose New simulation to run again.');return;}
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
    if(f===fleet){
      render();if(!f.failed&&(running||f.waiting||f.pending))schedule(f,f.waiting?350:950);
    }
  }
}
async function setup(){
  if(changing)return;
  changing=true;running=false;clearTimeout(timer);$('reset').disabled=true;$('play').disabled=true;
  msg('Preparing a private demo fleet…');
  try{
    const result=await api('sessions','POST',{count:Number($('size').value)});
    clearTimeout(timer);cancelAnimationFrame(frame);reviewGeneration++;
    fleet={...result,cabs:result.trips.map(t=>makeCab(t,result.xs,result.ys)),seconds:0,alerts:[],working:false};
    cityMap(result.xs,result.ys);makeLayers(fleet.cabs,select);$('roster').replaceChildren();$('review').hidden=true;
    for(const c of fleet.cabs){
      c.confirmed=0;c.sequence=1;c.row=element('button',undefined,'cabrow');c.row.style.setProperty('--cab',c.color);
      const top=element('span',undefined,'rowtop'),id=element('span',undefined,'cabid');
      id.append(element('i',undefined,'swatch'),element('strong','Cab '+c.cab));c.badge=element('span','On route','badge');
      top.append(id,c.badge);c.row.append(top,element('span',c.origin+' → '+c.destination,'desc'));
      c.row.onclick=()=>select(c.id);$('roster').append(c.row);
    }
    fleet.pending=fleet.cabs.map(c=>gps(fleet,c));select(fleet.cabs[0].id);$('connection').textContent='● Connected';
    msg('Ready. Start the simulation, then choose a cab and take a wrong turn.');schedule(fleet,0);
  }catch(e){msg(e.message+' Select New simulation to retry.');$('connection').textContent='Offline';}
  finally{changing=false;$('reset').disabled=false;if(fleet)render();}
}
$('reset').onclick=setup;
$('play').onclick=()=>{if(!fleet)return;running=!running;fleet.failed=false;render();
  msg(running?'Simulation running · each update advances five simulated seconds.':'Simulation paused.');schedule(fleet,0);};
$('wrong').onclick=()=>{divert(chosen());render();msg(running?'Wrong turn requested. Watch the GPS readings.':'Wrong turn queued. Press Start to drive.');};
$('return').onclick=()=>{rejoin(chosen());render();msg(running?'Cab is rejoining its planned route.':'Return requested. Press Start to drive.');};
setup();


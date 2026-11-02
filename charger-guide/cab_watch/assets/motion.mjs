const same=(a,b)=>a[0]===b[0]&&a[1]===b[1];
const dist=(a,b)=>Math.hypot(a[0]-b[0],a[1]-b[1]);

export function makeCab(trip,network){
  const point = [...trip.path[0]];
  return {...trip,nodes:trip.path,index:0,point,mode:'route',sequence:0,
          streak:0,offset:0,confirmed:0,trail:[point],network};
}
function awayFromRoute(cab,p){
  return Math.min(...cab.nodes.slice(1).map((b,i)=>{
    const a=cab.nodes[i], dx=b[0]-a[0],dy=b[1]-a[1];
    const t=Math.max(0,Math.min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/(dx*dx+dy*dy)));
    return dist(p,[a[0]+t*dx,a[1]+t*dy]);
  }));
}
function chooseBranch(cab){
  const node = cab.stops[cab.index];
  const choices = cab.network.roads.filter(edge=>edge.includes(node))
    .map(edge=>cab.network.nodes[edge.replace(node,'')])
    .filter(point=>awayFromRoute(cab,point)>0.01);
  return choices.sort((a,b)=>awayFromRoute(cab,b)-awayFromRoute(cab,a))[0];
}
export function divert(cab){
  if(cab.mode!=='route' && cab.mode!=='turn requested')return;
  cab.branch = same(cab.point,cab.nodes[cab.index]) && chooseBranch(cab);
  cab.mode = cab.branch?'detour':'turn requested';
}
export function rejoin(cab){
  if(cab.mode==='turn requested')cab.mode='route';
  else if(cab.mode==='detour')cab.mode='returning';
}
export function advance(cab){
  if(cab.mode==='arrived')return false;
  if(cab.mode==='route' && cab.index===cab.nodes.length-1){cab.mode='arrived';return false;}
  const drivingRoute=cab.mode==='route'||cab.mode==='turn requested';
  const target=drivingRoute?cab.nodes[cab.index+1]:cab.mode==='returning'?cab.nodes[cab.index]:cab.branch;
  const delta=dist(cab.point,target);
  cab.point=delta<=14?[...target]:cab.point.map((v,i)=>v+(target[i]-v)*14/delta);
  if(same(cab.point,target)){
    if(drivingRoute){
      cab.index++;
      if(cab.index===cab.nodes.length-1)cab.mode='arrived';
      else if(cab.mode==='turn requested')divert(cab);
    }else if(cab.mode==='returning'){cab.mode=cab.index===cab.nodes.length-1?'arrived':'route';}
  }
  cab.sequence++;
  return true;
}


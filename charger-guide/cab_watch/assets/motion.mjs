const same=(a,b)=>a[0]===b[0]&&a[1]===b[1];
const dist=(a,b)=>Math.hypot(a[0]-b[0],a[1]-b[1]);

export function makeCab(trip,xs,ys){
  const nodes=[trip.path[0]];
  for(let i=1;i<trip.path.length;i++){
    const a=trip.path[i-1], b=trip.path[i], horizontal=a[1]===b[1], axis=horizontal?0:1;
    const coords=(horizontal?xs:ys).filter(c=>(c-a[axis])*(c-b[axis])<=0 && c!==a[axis]);
    coords.sort((l,r)=>(l-r)*Math.sign(b[axis]-a[axis]));
    nodes.push(...coords.map(c=>horizontal?[c,a[1]]:[a[0],c]));
  }
  return {...trip,nodes,index:0,point:[...nodes[0]],mode:'route',sequence:0,
          streak:0,offset:0,trail:[[...nodes[0]]],xs,ys};
}
function awayFromRoute(cab,p){
  return Math.min(...cab.nodes.slice(1).map((b,i)=>{
    const a=cab.nodes[i], dx=b[0]-a[0],dy=b[1]-a[1];
    const t=Math.max(0,Math.min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/(dx*dx+dy*dy)));
    return dist(p,[a[0]+t*dx,a[1]+t*dy]);
  }));
}
function chooseBranch(cab){
  const p=cab.nodes[cab.index], xi=cab.xs.indexOf(p[0]),yi=cab.ys.indexOf(p[1]);
  const nearby=[[cab.xs[xi-1],p[1]],[cab.xs[xi+1],p[1]],[p[0],cab.ys[yi-1]],[p[0],cab.ys[yi+1]]];
  return nearby.filter(n=>n.every(Number.isFinite)).sort((a,b)=>awayFromRoute(cab,b)-awayFromRoute(cab,a))[0];
}
export function divert(cab){
  if(cab.mode!=='route')return;
  if(same(cab.point,cab.nodes[cab.index])){cab.branch=chooseBranch(cab);cab.mode='detour';}
  else cab.mode='turn requested';
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
      if(cab.mode==='turn requested'){cab.branch=chooseBranch(cab);cab.mode='detour';}
      else if(cab.index===cab.nodes.length-1)cab.mode='arrived';
    }else if(cab.mode==='returning'){cab.mode=cab.index===cab.nodes.length-1?'arrived':'route';}
  }
  cab.sequence++;
  return true;
}


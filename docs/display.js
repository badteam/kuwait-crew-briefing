'use strict';
const $=id=>document.getElementById(id),rooms=['A-1','B-2','C-3','D-4','E-5','F-6','H-8'];
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=s=>new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kuwait',hour:'2-digit',minute:'2-digit'}).format(new Date(s));
const df=s=>new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kuwait',day:'2-digit',month:'2-digit'}).format(new Date(s));
const params=new URLSearchParams(location.search),screen=rooms.includes(params.get('screen'))?params.get('screen'):'hall';
const orientation=params.get('orientation')||'auto',demo=params.get('demo')==='1';
document.body.dataset.orientation=['auto','portrait','landscape'].includes(orientation)?orientation:'auto';
document.body.classList.toggle('roomview',screen!=='hall');$('upcoming').hidden=screen!=='hall';
$('title').textContent=screen==='hall'?'Your journey starts here':'Briefing room '+screen;
const person='<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="7" r="4"/><path d="M4 22v-3a8 8 0 0 1 16 0v3M9 14l3 6 3-6"/></svg>';
let state=null,offset=0,lastFetch=0,busy=false;
function render(){
 const t=Date.now()+offset,flights=state?.flights||[];
 $('board').innerHTML=(screen==='hall'?rooms:[screen]).map(room=>{
  const f=flights.find(f=>f.room===room&&!f.done&&f.start&&new Date(f.end)>t);
  let content='<div class="idle">'+(state?'Waiting for the next flight':'Waiting for flight schedule')+'</div><p>Wishing you a pleasant flight</p>';
  if(f)content='<div class="flight" dir="ltr">'+esc(f.number)+'</div><div class="destination">'+esc(f.destination)+'</div><div class="sccm">'+person+'<b>SCCM</b><span>'+esc(f.sccm||'SCCM not assigned')+'</span></div><div class="times"><span>'+fmt(f.start)+' – '+fmt(f.end)+'</span><span class="countdown" data-end="'+esc(f.end)+'"></span></div>';
  return '<article class="card '+(f?'active':state?'free':'waiting')+'"><div class="cardtop"><span class="room" dir="ltr">'+room+'</span><span class="status">'+(f?'Briefing in progress':state?'Available':'Waiting for updates')+'</span></div>'+content+'</article>';
 }).join('');
 const queue=flights.filter(f=>!f.done&&!f.start),page=Math.floor(t/15000)%Math.max(1,Math.ceil(queue.length/4)),upcoming=queue.slice(page*4,page*4+4);
 $('queue').innerHTML=upcoming.length?'<div class="qrow"><span>Flight</span><span>Destination</span><span>Briefing</span><span>Status</span></div>'+upcoming.map(f=>'<div class="qrow"><span dir="ltr">'+esc(f.number)+'</span><span>'+esc(f.destination)+'</span><span dir="ltr">'+df(f.briefing)+' '+fmt(f.briefing)+'</span><span>'+(new Date(f.briefing)<=t?'Waiting for a room':'Upcoming')+'</span></div>').join(''):'<p>'+(state?'No flights in the queue':'Waiting for flight schedule updates')+'</p>';
 clock();
}
function clock(){
 $('clock').textContent=fmt(Date.now()+offset);
 document.querySelectorAll('[data-end]').forEach(el=>{const n=Math.max(0,Math.ceil((new Date(el.dataset.end)-Date.now()-offset)/1000));el.textContent='Remaining '+Math.floor(n/60)+':'+String(n%60).padStart(2,'0')});
 if(!demo&&lastFetch&&Date.now()-lastFetch>20000){document.body.classList.add('stale');$('notice').textContent='Updates interrupted — please check with the flight dispatcher.'}
}
async function refresh(){
 if(demo||busy)return;
 const endpoint=localStorage.getItem('crew-endpoint'),token=sessionStorage.getItem('crew-token');
 if(!endpoint||!token){state=null;lastFetch=0;$('notice').textContent='Waiting for flight schedule updates';$('updated').textContent='';render();return}
 busy=true;
 try{
  const res=await fetch(endpoint+'/api/state',{headers:{Authorization:'Bearer '+token},signal:AbortSignal.timeout(12000)});
  if(!res.ok)throw Error('offline');
  state=await res.json();offset=new Date(state.now)-Date.now();lastFetch=Date.now();document.body.classList.remove('stale');
  $('notice').textContent='';$('updated').textContent='Last updated '+fmt(Date.now()+offset);render();
 }catch{document.body.classList.add('stale');$('notice').textContent='Updates interrupted — please check with the flight dispatcher.'}
 finally{busy=false}
}
if(demo){
 const t=Date.now();
 state={flights:[...rooms.map((r,i)=>({id:'demo-'+i,room:r,number:'DEMO '+(101+i),destination:['Cairo','Dubai','London','Bangkok','Riyadh','Doha','Jeddah'][i],sccm:'Demo crew '+(i+1),start:new Date(t-60000).toISOString(),end:new Date(t+540000).toISOString(),done:0})),{number:'DEMO 108',destination:'Demo flight',briefing:new Date(t+600000).toISOString(),done:0}]};
 $('notice').textContent='Demo preview — not operational data';$('updated').textContent='Demo data';
}
render();refresh();setInterval(clock,1000);setInterval(()=>{if(demo)render();else refresh()},5000);

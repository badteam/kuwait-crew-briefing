'use strict';
const $=id=>document.getElementById(id),rooms=['A-1','B-2','C-3','D-4','E-5','F-6','H-8'],screens=['hall',...rooms];
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=s=>s?new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kuwait',hour:'2-digit',minute:'2-digit'}).format(new Date(s)):'';
const fmtUtc=s=>s?new Intl.DateTimeFormat('en-GB',{timeZone:'UTC',hour:'2-digit',minute:'2-digit'}).format(new Date(s)):'';
const df=s=>s?new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kuwait',day:'2-digit',month:'short'}).format(new Date(s)):'';
const screenName=s=>s==='hall'?'Main hall':'Room '+s;
const store={get:(k,s=localStorage)=>{try{return s.getItem(k)||''}catch{return ''}},set:(k,v,s=localStorage)=>{try{s.setItem(k,v)}catch{}},del:(k,s=localStorage)=>{try{s.removeItem(k)}catch{}}};
let endpoint=store.get('crew-endpoint')||window.CREW_API||'',token=store.get('crew-admin-token',sessionStorage),state=null,payload=null,offset=0,lastFetch=0,editing=false;
$('endpoint').value=endpoint;
screens.forEach(s=>{const o=document.createElement('option');o.value=s;o.textContent=screenName(s);$('pairScreen').append(o)});
const displayURL=new URL('./',location.href).href;$('displayLink').href=displayURL;$('displayLink').textContent=displayURL;
const person='<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="7" r="4"/><path d="M4 22v-3a8 8 0 0 1 16 0v3M9 14l3 6 3-6"/></svg>';
const crewHtml=f=>{const l=f.crew&&f.crew.length?f.crew:[];return l.length?'<div class="crewlist">'+l.map(p=>'<div class="sccm role-'+esc(p.role).toLowerCase()+'">'+person+'<b>'+esc(p.role)+'</b><span>'+esc(p.name)+'</span></div>').join('')+'</div>':'<div class="sccm">'+person+'<b>SCCM</b><span>SCCM not assigned</span></div>'};
function notice(text,error=false){$('notice').textContent=text;$('notice').className=error?'error':''}
async function api(path,body){
 const res=await fetch(endpoint+path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},...(body?{body:JSON.stringify(body)}:{}),signal:AbortSignal.timeout(30000)});
 let data;try{data=await res.json()}catch{throw Error('The service returned an invalid response.')}
 if(!res.ok){if(res.status===401&&path!=='/api/login'){token='';store.del('crew-admin-token',sessionStorage);showSetup(true)}throw Error(data.error||'Unable to connect.')}
 return data}
function showSetup(on){$('setup').hidden=!on;$('admin').hidden=on;$('logout').hidden=on}
function feedPanel(){
 const f=state.feed||{},names={aerodatabox:'AeroDataBox',flightaware:'FlightAware'};$('provider').value=f.provider||'aerodatabox';[...$('provider').options].forEach(o=>{o.textContent=names[o.value]+((f.providers||{})[o.value]?'':' (no key)')});
 const rows=[['Source',names[f.provider]||'—'],['Last successful update',f.last_success?df(f.last_success)+' '+fmt(f.last_success):'Not yet'],['Flights in last update',f.count??'—'],['Status',f.key_configured===false?'Flight data key not configured on the server':f.ok===false?'Last attempt failed: '+(f.error||'unknown error'):f.ok?'OK':'Waiting for first update'],['Automatic refresh','Every '+(f.interval_hours||6)+' hours (next 12 hours of departures)'],['AeroDataBox this month',(f.units_used??0)+' of '+(f.units_budget??'—')+' units'],['FlightAware this month',(f.fa_pages_used??0)+' of '+(f.fa_pages_budget??'—')+' pages'],['SCCM names waiting for flights',state.pending_crew??0]];
 $('feed').innerHTML=rows.map(([k,v])=>'<div><span>'+esc(k)+'</span><b class="'+(String(v).startsWith('Last attempt failed')||String(v).includes('not configured')?'bad':'')+'">'+esc(v)+'</b></div>').join('');
 const old=f.last_success&&Date.now()+offset-new Date(f.last_success)>8*3600e3;
 $('updated').textContent=f.last_success?'Flight data updated '+df(f.last_success)+' '+fmt(f.last_success)+(old?' (out of date)':''):'No flight data yet';
}
function flightsTable(){
 const t=Date.now()+offset;
 const rows=state.flights.filter(f=>!f.done||f.end&&new Date(f.end)>t-3600e3);
 $('flights').innerHTML='<thead><tr><th>Flight</th><th>Date</th><th>Destination</th><th>Aircraft</th><th>Departure (GMT)</th><th>Reporting (Kuwait)</th><th>Room</th><th>SCCM</th></tr></thead><tbody>'+(rows.length?rows.map(f=>{
  const status=f.done?(f.end?'Finished':'Closed'):f.start?(new Date(f.end)>t?'In '+f.room:'Finished'):f.needs_type?'<span class="bad">Needs aircraft type</span>':new Date(f.briefing)<=t?'Waiting for room':'Upcoming';
  const aircraft=f.needs_type&&!f.start?'<select data-aircraft="'+esc(f.id)+'"><option value="">'+esc(f.aircraft||'Unknown')+' — choose</option><option value="A320">A320</option><option value="A321">A321</option><option value="B777">B777</option><option value="A330">A330</option></select>':esc(f.aircraft||'—');
  return '<tr class="'+(f.needs_type?'warn':'')+'"><td dir="ltr">'+esc(f.label||f.number)+'</td><td>'+df(f.departure||f.source)+'</td><td>'+esc(f.destination)+'</td><td>'+aircraft+'</td><td dir="ltr">'+(f.departure?(f.start?fmtUtc(f.departure):'<form class="depform" data-id="'+esc(f.id)+'"><input name="t" value="'+fmtUtc(f.departure)+'" size="5" maxlength="16" title="Departure in GMT, HH:MM"><button>Set</button></form>')+(f.departure_manual?'<small class="manual">edited</small>':''):'—')+'</td><td dir="ltr">'+(f.briefing?fmt(f.briefing)+(f.briefing_manual?'<small class="manual">list</small>':''):'—')+'</td><td>'+status+'</td><td><form class="sccmform" data-id="'+esc(f.id)+'"><input name="sccm" value="'+esc(f.sccm)+'" placeholder="Name / Name (FSS)" title="Separate names with /. Add (CCI) or (FSS) after a name for its title." maxlength="120"><button>Save</button></form></td></tr>'}).join(''):'<tr><td colspan="8">No flights yet. They appear after the next schedule update.</td></tr>')+'</tbody>';
}
function board(){
 const t=Date.now()+offset;
 $('board').innerHTML=rooms.map(room=>{const f=state.flights.find(f=>f.room===room&&!f.done&&f.start&&new Date(f.end)>t);
  const content=f?'<div class="flight" dir="ltr">'+esc(f.label||f.number)+'</div><div class="destination">'+esc(f.destination)+'</div>'+crewHtml(f)+'<div class="times"><span>'+fmt(f.start)+' – '+fmt(f.end)+'</span><span class="countdown" data-end="'+esc(f.end)+'"></span></div><button class="extend" data-extend="'+esc(f.id)+'">Extend by 5 minutes</button>':'<div class="idle">Available</div>';
  return '<article class="card '+(f?'active':'free')+'"><div class="cardtop"><span class="room" dir="ltr">'+room+'</span><span class="status">'+(f?'Briefing in progress':'Available')+'</span></div>'+content+'</article>'}).join('');
}
function displays(){
 const list=state.displays||[],opt=(vals,cur,label)=>vals.map(v=>'<option value="'+v+'"'+(v===cur?' selected':'')+'>'+esc(label(v))+'</option>').join('');
 $('displays').innerHTML=list.length?'<thead><tr><th>Name</th><th>Screen</th><th>Orientation</th><th>Last seen</th><th></th></tr></thead><tbody>'+list.map(d=>'<tr data-display="'+esc(d.id)+'"><td>'+esc(d.label)+'</td><td><select name="screen">'+opt(screens,d.screen,screenName)+'</select></td><td><select name="orientation">'+opt(['auto','portrait','landscape'],d.orientation,v=>({auto:'Automatic',portrait:'Portrait',landscape:'Landscape'})[v])+'</select></td><td>'+(d.last_seen?df(d.last_seen*1000)+' '+fmt(d.last_seen*1000):'—')+'</td><td><button data-save>Save</button> <button data-remove>Remove</button></td></tr>').join('')+'</tbody>':'<tbody><tr><td>No displays paired yet.</td></tr></tbody>';
}
function render(){if(!state)return;if(!editing)feedPanel();if(!editing){flightsTable();displays()}board();tickClock()}
function tickClock(){
 $('clock').textContent=fmt(Date.now()+offset);
 document.querySelectorAll('[data-end]').forEach(el=>{const n=Math.max(0,Math.ceil((new Date(el.dataset.end)-Date.now()-offset)/1000));el.textContent='Remaining '+Math.floor(n/60)+':'+String(n%60).padStart(2,'0')});
 if(lastFetch&&Date.now()-lastFetch>20000)$('connection').textContent='Connection lost';
}
async function refresh(){if(!token)return;try{state=await api('/api/state');offset=new Date(state.now)-Date.now();lastFetch=Date.now();$('connection').textContent='● Connected';showSetup(false);render()}catch(e){$('connection').textContent='Connection lost';if(token)notice(e.message,true)}}
// Pause table redraws while an input in the tables has focus, so typing is not lost.
document.addEventListener('focusin',e=>{editing=!!e.target.closest('#flights,#displays,#feedPanel')});
document.addEventListener('focusout',()=>setTimeout(()=>{editing=!!document.activeElement?.closest('#flights,#displays,#feedPanel')},0));
$('login').onsubmit=async e=>{e.preventDefault();try{const raw=$('endpoint').value||window.CREW_API||'';const u=new URL(raw);if(u.protocol!=='https:'&&!['localhost','127.0.0.1'].includes(u.hostname))throw Error('Use a secure HTTPS URL.');endpoint=u.origin;const d=await api('/api/login',{password:$('password').value});token=d.token;store.set('crew-admin-token',token,sessionStorage);if(endpoint!==window.CREW_API)store.set('crew-endpoint',endpoint);$('password').value='';notice('');await refresh()}catch(e){notice(e instanceof TypeError&&!e.message.includes('URL')?'Unable to reach the service.':e.message,true)}};
$('logout').onclick=async()=>{try{await api('/api/logout',{})}catch{}token='';state=null;store.del('crew-admin-token',sessionStorage);showSetup(true);notice('Signed out')};
$('useProvider').onclick=async()=>{try{const r=await api('/api/provider',{provider:$('provider').value});notice('Flight source set to '+r.provider+'. Flights refresh from it within a minute.');await refresh()}catch(e){notice(e.message,true)}};
$('sync').onclick=async()=>{$('sync').disabled=true;notice('Refreshing flights…');try{const f=await api('/api/sync',{});notice('Flight schedule updated: '+f.count+' Kuwait Airways departures in the next 12 hours.');await refresh()}catch(e){notice(e.message,true)}finally{$('sync').disabled=false}};
$('flights').addEventListener('submit',async e=>{const form=e.target.closest('.sccmform');if(!form)return;e.preventDefault();const btn=form.querySelector('button');btn.disabled=true;try{await api('/api/sccm',{id:form.dataset.id,sccm:form.sccm.value});notice('SCCM saved.');editing=false;await refresh()}catch(err){notice(err.message,true)}finally{btn.disabled=false}});
$('flights').addEventListener('submit',async e=>{const form=e.target.closest('.depform');if(!form)return;e.preventDefault();const btn=form.querySelector('button');btn.disabled=true;try{await api('/api/departure',{id:form.dataset.id,time:form.t.value});notice('Departure time saved. Reporting time recalculated; automatic updates will keep this time.');editing=false;await refresh()}catch(err){notice(err.message,true)}finally{btn.disabled=false}});
$('flights').addEventListener('change',async e=>{const sel=e.target.closest('[data-aircraft]');if(!sel||!sel.value)return;try{await api('/api/aircraft',{id:sel.dataset.aircraft,aircraft:sel.value});notice('Aircraft type set. Reporting time calculated.');editing=false;await refresh()}catch(err){notice(err.message,true)}});
$('board').onclick=async e=>{const btn=e.target.closest('[data-extend]');if(!btn)return;btn.disabled=true;try{await api('/api/extend',{id:btn.dataset.extend});await refresh()}catch(err){notice(err.message,true);btn.disabled=false}};
$('pair').onsubmit=async e=>{e.preventDefault();try{await api('/api/pair/approve',{code:$('pairCode').value,screen:$('pairScreen').value,orientation:$('pairOrientation').value,label:$('pairLabel').value});notice('Display paired. It will switch to '+screenName($('pairScreen').value)+' within a few seconds.');$('pairCode').value='';$('pairLabel').value='';await refresh()}catch(err){notice(err.message,true)}};
$('displays').onclick=async e=>{const row=e.target.closest('[data-display]');if(!row)return;
 if(e.target.matches('[data-save]')){try{await api('/api/displays/update',{id:row.dataset.display,screen:row.querySelector('[name=screen]').value,orientation:row.querySelector('[name=orientation]').value});notice('Display updated.');editing=false;await refresh()}catch(err){notice(err.message,true)}}
 if(e.target.matches('[data-remove]')&&confirm('Remove this display? It will show a new pairing code.')){try{await api('/api/displays/remove',{id:row.dataset.display});editing=false;await refresh()}catch(err){notice(err.message,true)}}};
$('kind').addEventListener('change',()=>{$('fullList').closest('label').hidden=$('kind').value!=='roster'});
$('file').onchange=$('kind').onchange=()=>{payload=null;$('commit').disabled=true;$('previewTable').innerHTML=''};
$('preview').onclick=async()=>{try{const f=$('file').files[0];if(!f)throw Error('Choose a file first.');if(f.size>3000000)throw Error('Maximum file size is 3 MB.');const bytes=new Uint8Array(await f.arrayBuffer());let raw='';for(let i=0;i<bytes.length;i+=8192)raw+=String.fromCharCode(...bytes.subarray(i,i+8192));payload={name:f.name,data:btoa(raw),kind:$('kind').value,full_list:$('fullList').checked};const result=await api('/api/preview',payload);const cols=payload.kind==='roster'?['number','reporting','destination','crew_text']:payload.kind==='crew'?['number','date','sccm']:['number','destination','aircraft','source','briefing','sccm'];const show=(c,v)=>c==='reporting'?(df(v)+' '+fmt(v)):v;$('previewTable').innerHTML='<table><thead><tr>'+cols.map(c=>'<th>'+esc(c)+'</th>').join('')+'</tr></thead><tbody>'+result.rows.slice(0,100).map(r=>'<tr>'+cols.map(c=>'<td>'+esc(show(c,r[c]))+'</td>').join('')+'</tr>').join('')+'</tbody></table>';$('commit').disabled=false;notice('Previewed '+result.rows.length+' rows. Review them, then select Import data.')}catch(e){payload=null;$('commit').disabled=true;notice(e.message,true)}};
$('commit').onclick=async()=>{if(!payload)return;$('commit').disabled=true;try{const r=await api('/api/import',payload);payload=null;$('previewTable').innerHTML='';await refresh();notice(r.added!==undefined?'Reporting list saved: '+r.matched+' flights updated, '+r.added+' added'+(r.closed?', '+r.closed+' hidden (not in the list)':'')+'.':r.pending!==undefined?'SCCM names saved: '+r.matched+' matched now, '+r.pending+' waiting for their flights.':'Saved '+r.count+' rows.')}catch(e){notice(e.message,true);$('commit').disabled=false}};
showSetup(!token);setInterval(tickClock,1000);setInterval(()=>{if(!editing)refresh()},10000);if(token)refresh();

(() => {
"use strict";
const $ = id => document.getElementById(id);
let models = [];
let agents = [];
let token = sessionStorage.getItem("modelharbor_token") || "";
let view = "overview";
const fmt = n => { if (!Number.isFinite(n)) return "—"; if (n === 0) return "0 B"; const u = ["B","KB","MB","GB","TB","PB"]; const i = Math.min(Math.floor(Math.log(n)/Math.log(1000)),u.length-1); return (n/Math.pow(1000,i)).toFixed(i ? 1 : 0)+" "+u[i]; };
const text = (tag,value,cls) => { const e=document.createElement(tag); e.textContent=value; if(cls)e.className=cls; return e; };
function showNotice(message){ $("notice").textContent=message; $("notice").hidden=!message; }
function setStatus(online){ $("status").textContent=online?"● Connected":"● Disconnected"; $("status").classList.toggle("online",online); }
function openAuth(){ $("token").value=token; $("auth-dialog").showModal(); }
function switchView(next){view=next;document.querySelectorAll(".tab").forEach(b=>b.classList.toggle("active",b.dataset.view===next));document.querySelectorAll(".view").forEach(e=>e.hidden=e.id!==next+"-view");$("heading").textContent=({overview:"Model overview",library:"Model library",devices:"Connected sources"})[next];}
async function searchProvider(){
 const provider=$("provider").value, q=$("provider-query").value.trim(), target=$("provider-results");
 target.replaceChildren();
 if(q.length<3){target.append(text("p","Enter at least 3 characters.","muted"));return;}
 const button=$("provider-search");button.disabled=true;target.append(text("p","Searching public model listings…","muted"));
 try{
  const response=await fetch("/api/discovery/"+encodeURIComponent(provider)+"?q="+encodeURIComponent(q),{headers:{"X-ModelHarbor-Token":token},cache:"no-store"});
  if(!response.ok)throw Error("Search unavailable (HTTP "+response.status+")");
  const payload=await response.json();target.replaceChildren();
  if(!payload.results.length){target.append(text("p","No candidates found. Try a shorter model name.","muted"));return;}
  payload.results.forEach(item=>{
   const a=document.createElement("a");a.className="provider-result";
   a.href=item.url;a.target="_blank";a.rel="noopener noreferrer";
   a.append(text("strong",item.name),text("span",item.provider+" · Unverified candidate","model-sub"));
   if(item.provider==="huggingface"){const button=text("button","Browse repository files","repo-action");button.type="button";button.addEventListener("click",e=>{e.preventDefault();$("repo-name").value=item.id;browseRepository();});target.append(button);}
   target.append(a);
  });
 }catch(err){target.replaceChildren(text("p",err.message,"muted"));}
 finally{button.disabled=false;}
}

$("provider-search").addEventListener("click",searchProvider);

let repoListing=null;
function repoFileRow(file){
 const card=text("div","","repo-file");
 const name=text("strong",file.name,"repo-filename");
 const secondary=text("span",file.path+" · "+fmt(file.size_bytes),"model-sub");
 const local=models.filter(m=>m.name.toLowerCase()===file.name.toLowerCase()&&m.kind!=="ollama");
 const sameSize=local.some(m=>Number(m.size_bytes)===file.size_bytes);
 const badge=text("span",sameSize?"Filename + size match (unverified)":local.length?"Filename match (unverified)":"Not matched by filename",sameSize?"match-possible":"model-sub");
 const link=document.createElement("a");link.href=file.url;link.target="_blank";link.rel="noopener noreferrer";link.textContent="View file ↗";link.className="repo-link";
 card.append(name,secondary,badge);
 if(file.sha256)card.append(text("span","Remote SHA-256: "+file.sha256,"model-sub"));
 card.append(link);
 return card;
}
function displayRepoFiles(){
 const area=$("repo-files");area.replaceChildren();
 if(!repoListing)return;
 const term=$("repo-filter").value.trim().toLowerCase().replace(/[_-]/g," ");
 const list=repoListing.files.filter(f=>f.path.toLowerCase().replace(/[_-]/g," ").includes(term));
 $("repo-status").textContent=list.length+" files shown of "+repoListing.files.length+" model files"+(repoListing.complete?"":" · Partial listing; some files may be missing");
 if(!list.length)area.append(text("p","No model files found for this search.","muted"));
 for(const file of list.slice(0,500))area.append(repoFileRow(file));
 if(list.length>500)area.append(text("p","Showing first 500 matching files. Narrow the filter.","muted"));
}
async function browseRepository(){
 const repo=$("repo-name").value.trim();
 if(!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$/.test(repo)||repo.includes("..")){
  $("repo-status").textContent="Enter a valid owner/repository name.";return;
 }
 const btn=$("repo-browse");btn.disabled=true;repoListing=null;$("repo-files").replaceChildren();$("repo-status").textContent="Loading repository file metadata…";
 try{
  const response=await fetch("/api/huggingface/repository?repo="+encodeURIComponent(repo),{headers:{"X-ModelHarbor-Token":token},cache:"no-store"});
  if(!response.ok)throw Error("Repository lookup failed (HTTP "+response.status+")");
  repoListing=await response.json();displayRepoFiles();
 }catch(err){$("repo-status").textContent=err.message;}
 finally{btn.disabled=false;}
}
$("repo-browse").addEventListener("click",browseRepository);
$("repo-filter").addEventListener("input",displayRepoFiles);
function modelRow(m){const b=text("button","", "model");b.type="button";b.append(text("span","◈","glyph"));const info=text("span","","model-text");info.append(text("div",m.name,"model-name"),text("div",m.kind+" · "+m.device+" · "+fmt(m.size_bytes)+(m.possible_duplicate?" · Possible duplicate":"")+(m.hard_link?" · Hard link":""),"model-sub"));b.append(info,text("span","›","chevron"));b.addEventListener("click",()=>details(m));return b;}
function fill(target,items,empty){target.replaceChildren();if(!items.length)target.append(text("p",empty,"muted"));else items.forEach(m=>target.append(modelRow(m)));}
function details(m){$("detail-title").textContent=m.name;const dl=$("detail-fields");dl.replaceChildren();for(const [k,v] of Object.entries({Type:m.kind,Device:m.device,Size:fmt(m.size_bytes),Family:(m.family_guess||"Unknown")+" (unverified)",Duplicates:m.possible_duplicate?"Possible match — verify hashes":"No matching name and size",Path:m.path})){dl.append(text("dt",k),text("dd",v));}$("provider-query").value=m.name.replace(/\.(safetensors|ckpt|gguf|pt|pth|bin)$/i,"").replace(/[_-]+/g," ").slice(0,120);$("provider-results").replaceChildren();$("repo-files").replaceChildren();$("repo-status").textContent="";$("repo-filter").value="";$("detail-dialog").showModal();}
function options(id,values,label){const el=$(id),old=el.value;el.replaceChildren(new Option(label,""));values.forEach(v=>el.add(new Option(v,v)));el.value=values.includes(old)?old:"";}
function render(){
 $("count").textContent=models.length.toLocaleString();
 $("size").textContent=fmt(models.reduce((n,m)=>n+(m.hard_link?0:Number(m.size_bytes)||0),0));
 const devices=[...new Set(models.map(m=>m.device))].sort();
 const physical=[...new Set(models.map(m=>m.physical_device||m.device))];
 $("devices-count").textContent=physical.length;
 fill($("recent"),models.slice(0,8),"No models indexed yet. Run the Windows inventory agent.");
 const counts=new Map();models.forEach(m=>counts.set(m.kind,(counts.get(m.kind)||0)+1));
 $("types").replaceChildren();[...counts].sort((a,b)=>b[1]-a[1]).slice(0,10).forEach(([kind,count])=>{const row=text("div");const line=text("div","","type-line");line.append(text("span",kind),text("span",String(count)));const bar=text("div","","bar");const inner=text("span");inner.style.width=(count/Math.max(models.length,1)*100)+"%";bar.append(inner);row.append(line,bar);$("types").append(row);});
 if(!counts.size)$("types").append(text("p","Model categories will appear after the first inventory scan.","muted"));
 options("kind",[...counts.keys()].sort(),"All types");options("device",devices,"All sources");options("family",[...new Set(models.map(m=>m.family_guess||"Unknown"))].sort(),"All families");
 const deviceList=$("device-list");deviceList.replaceChildren();
 agents.forEach(a=>{const card=text("div","","agent-card");const state=text("span",a.online?"● Online":"● Offline",a.online?"agent-online":"agent-offline");card.append(text("strong",a.device),state,text("div","ComfyUI: "+a.comfyui_count+" · Ollama: "+(a.ollama_count ?? "unavailable"),"model-sub"),text("div","Last sync: "+new Date(a.last_seen*1000).toLocaleString(),"model-sub"));deviceList.append(card);});
 devices.forEach(device=>{const matching=models.filter(m=>m.device===device);const b=text("button","","model");b.append(text("span","▣","glyph"));const info=text("span","","model-text");info.append(text("div",device,"model-name"),text("div",matching.length+" models · "+fmt(matching.reduce((n,m)=>n+(m.hard_link?0:m.size_bytes),0)),"model-sub"));b.append(info,text("span","›","chevron"));b.onclick=()=>{switchView("library");$("device").value=device;filter();};deviceList.append(b);});
 if(!devices.length)deviceList.append(text("p","No inventory sources registered.","muted"));
 filter();
}
function filter(){
 const q=$("search").value.trim().toLowerCase(),kind=$("kind").value,device=$("device").value;
 const family=$("family").value, duplicates=$("duplicates").checked, sort=$("sort").value;
 const subset=models.filter(m=>(!q||(m.name+" "+m.path).toLowerCase().includes(q))&&(!kind||m.kind===kind)&&(!device||m.device===device)&&(!family||m.family_guess===family)&&(!duplicates||m.possible_duplicate));
 subset.sort((a,b)=>sort==="size-desc"?b.size_bytes-a.size_bytes:sort==="size-asc"?a.size_bytes-b.size_bytes:sort==="kind"?a.kind.localeCompare(b.kind)||a.name.localeCompare(b.name):a.name.localeCompare(b.name));
 $("results").textContent=subset.length+" results";
 fill($("library"),subset.slice(0,500),"No matching models.");
 if(subset.length>500)$("library").append(text("p","Showing first 500 results. Narrow your filters to see more.","muted"));
}
async function refresh(){if(!token){openAuth();return;} $("refresh").disabled=true;try{const response=await fetch("/api/models",{headers:{"X-ModelHarbor-Token":token},cache:"no-store"});if(response.status===401){token="";sessionStorage.removeItem("modelharbor_token");setStatus(false);showNotice("Token rejected. Enter your configured API token.");openAuth();return;}if(!response.ok)throw Error("Server returned HTTP "+response.status);const data=await response.json();if(!Array.isArray(data))throw Error("Unexpected inventory response");models=data;try{const agentsResponse=await fetch("/api/agents",{headers:{"X-ModelHarbor-Token":token},cache:"no-store"});agents=agentsResponse.ok?await agentsResponse.json():[];}catch(_){agents=[];}setStatus(true);showNotice("");render();}catch(err){setStatus(false);showNotice("Unable to load inventory: "+err.message);}finally{$("refresh").disabled=false;}}
document.querySelectorAll(".tab").forEach(b=>b.addEventListener("click",()=>switchView(b.dataset.view)));
document.querySelectorAll("[data-go]").forEach(b=>b.addEventListener("click",()=>switchView(b.dataset.go)));
["search","kind","device","family","sort","duplicates"].forEach(id=>$(id).addEventListener(id==="search"?"input":"change",filter));
$("refresh").onclick=refresh;$("settings").onclick=openAuth;
$("auth-form").addEventListener("submit",e=>{e.preventDefault();token=$("token").value.trim();sessionStorage.setItem("modelharbor_token",token);$("auth-dialog").close();refresh();});
$("cancel-auth").onclick=()=>$("auth-dialog").close();
$("close-detail").onclick=$("detail-done").onclick=()=>$("detail-dialog").close();
if(token)refresh();else openAuth();
})();
(() => {
"use strict";
const $ = id => document.getElementById(id);
let models = [];
let agents = [];
let selectedMatches = [];
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
   target.append(a);
   if(item.provider==="huggingface"){const button=text("button","Browse repository files","repo-action");button.type="button";button.addEventListener("click",()=>{$("repo-name").value=item.id;browseRepository();});target.append(button);}
  });
 }catch(err){target.replaceChildren(text("p",err.message,"muted"));}
 finally{button.disabled=false;}
}

$("provider-search").addEventListener("click",searchProvider);

let repoListing=null;
function selectedRemote(file){
 return selectedMatches.filter(s=>repoListing&&s.repository===repoListing.repository&&s.remote_path===file.path);
}
async function apiRequest(path,method="GET",body){
 const response=await fetch(path,{method,headers:{"X-ModelHarbor-Token":token,...(body?{"Content-Type":"application/json"}:{})},
  body:body?JSON.stringify(body):undefined,cache:"no-store"});
 if(!response.ok){let detail="HTTP "+response.status;try{const data=await response.json();if(typeof data.detail==="string")detail=data.detail;}catch(_){}
  throw Error(detail);}
 return response.json();
}
async function requestVerification(file,local,status,button,controls){
 button.disabled=true;status.textContent="Verification queued; Windows hash worker will read the selected file…";
 try{
  const job=await apiRequest("/api/hash-jobs","POST",{
   device:local.device,path:local.path,repository:repoListing.repository,remote_path:file.path
  });
  async function poll(){
   if(!document.getElementById("detail-dialog").open)return;
   try{
    const current=await apiRequest("/api/hash-jobs/"+encodeURIComponent(job.id));
    if(current.state==="queued"||current.state==="running"){
     status.textContent=current.state==="running"?"Computing SHA-256 on Windows…":"Waiting for Windows hash worker…";
     window.setTimeout(poll,5000);return;
    }
    if(current.state==="verified"){
     status.textContent="SHA-256 verified — exact bytes match. Confirm to associate this file.";
     const confirm=text("button","Select verified match","primary");confirm.type="button";
     controls.append(confirm);
     confirm.onclick=async()=>{
      confirm.disabled=true;
      try{
       const chosen=await apiRequest("/api/hash-jobs/"+encodeURIComponent(job.id)+"/select","POST");
       selectedMatches=selectedMatches.filter(s=>!(s.device===chosen.device&&s.path===chosen.path));
       selectedMatches.push(chosen);
       displayRepoFiles();
      }catch(err){status.textContent="Could not select match: "+err.message;confirm.disabled=false;}
     };
    }else{
     status.textContent=current.state==="mismatch"?"SHA-256 mismatch. These files are not identical.":current.state==="stale"?"Local file inventory changed. Refresh and verify again.":"Verification failed: "+(current.error||"Unknown error");
     button.disabled=false;
    }
   }catch(err){status.textContent="Status lookup failed: "+err.message;button.disabled=false;}
  }
  poll();
 }catch(err){status.textContent="Verification could not start: "+err.message;button.disabled=false;}
}
function repoFileRow(file){
 const card=text("div","","repo-file");
 card.append(text("strong",file.name,"repo-filename"),
   text("span",file.path+" · "+fmt(file.size_bytes),"model-sub"));
 const chosen=selectedRemote(file);
 if(chosen.length){
  card.append(text("span","✓ Selected exact match: "+chosen.map(s=>s.device+" · "+s.path).join(", "),"match-verified"));
 }
 const byName=models.filter(m=>m.name.toLowerCase()===file.name.toLowerCase()&&m.device.endsWith(":comfyui"));
 const sameSize=models.filter(m=>m.device.endsWith(":comfyui")&&Number(m.size_bytes)===file.size_bytes);
 const candidates=sameSize.sort((a,b)=>(Number(b.name.toLowerCase()===file.name.toLowerCase())-Number(a.name.toLowerCase()===file.name.toLowerCase()))||a.name.localeCompare(b.name));
 card.append(text("span",byName.some(m=>m.size_bytes===file.size_bytes)?"Filename + size match (unverified)":byName.length?"Filename match (size differs)":candidates.length?"Other local files have the same size; select one to verify":"No similar local file",candidates.length?"match-possible":"model-sub"));
 if(file.sha256)card.append(text("span","Remote SHA-256 available","model-sub"));
 const link=document.createElement("a");link.href=file.url;link.target="_blank";link.rel="noopener noreferrer";
 link.textContent="View file ↗";link.className="repo-link";card.append(link);
 if(candidates.length && file.sha256){
  const controls=text("div","","verify-controls");
  const picker=document.createElement("select");picker.setAttribute("aria-label","Choose local file to verify");
  candidates.slice(0,300).forEach((m,i)=>picker.add(new Option(m.name+" · "+m.device+" · "+m.path,i)));
  const verify=text("button","Verify SHA-256","primary");verify.type="button";
  const status=text("p","","muted");status.setAttribute("role","status");
  verify.onclick=()=>requestVerification(file,candidates[Number(picker.value)],status,verify,controls);
  controls.append(picker,verify);card.append(controls,status);
 }else if(candidates.length && !file.sha256){
  card.append(text("span","No authoritative remote SHA-256 available. Verification unavailable.","model-sub"));
 }
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
function modelRow(m){const b=text("button","", "model");b.type="button";b.append(text("span","◈","glyph"));const info=text("span","","model-text");info.append(text("div",m.name,"model-name"),text("div",m.kind+" · "+m.device+" · "+fmt(m.size_bytes)+(m.possible_duplicate?" · Possible duplicate":"")+(m.hard_link?" · Hard link":"")+(m.source?" · ✓ "+providerName(m.source.provider):"")+(m.update?" · ⬆ Update":""),"model-sub"));b.append(info,text("span","›","chevron"));b.addEventListener("click",()=>details(m));return b;}
function fill(target,items,empty){target.replaceChildren();if(!items.length)target.append(text("p",empty,"muted"));else items.forEach(m=>target.append(modelRow(m)));}
const providerName=p=>({civitai:"Civitai",huggingface:"Hugging Face",ollama:"Ollama"})[p]||p;
function linkRow(dl,label,caption,url){const a=document.createElement("a");a.href=url;a.target="_blank";a.rel="noopener noreferrer";a.textContent=caption;const dd=document.createElement("dd");dd.append(a);dl.append(text("dt",label),dd);}
function downloadStatus(j){
 if(!j)return "Saved next to the current file after its SHA-256 is checked. Nothing is deleted.";
 if(j.state==="queued")return "Waiting for the Windows agent…";
 if(j.state==="running")return j.provider==="ollama"?"Ollama is pulling the newer build…":"Downloading "+fmt(j.bytes_done)+(j.size_bytes?" of "+fmt(j.size_bytes):"")+"…";
 if(j.state==="done")return j.provider==="ollama"?"Pulled the newer build.":"Saved as "+j.result_path+". The old file was kept.";
 return "Download failed: "+(j.error||"unknown error");
}
async function renderDownload(m){
 const box=$("download-box");box.replaceChildren();if(!m.update)return;
 const button=text("button","Download update","primary");button.type="button";const status=text("p","","muted");box.append(button,status);
 let job=null;
 const show=j=>{job=j||job;status.textContent=downloadStatus(job);const active=job&&(job.state==="queued"||job.state==="running");button.disabled=!!active||!!job&&job.state==="done";if(active)setTimeout(poll,3000);};
 async function poll(){if(!$("detail-dialog").open||$("detail-title").textContent!==m.name)return;try{show((await apiRequest("/api/downloads")).find(x=>x.id===job.id));}catch(err){status.textContent=err.message;}}
 button.addEventListener("click",async()=>{button.disabled=true;try{show(await apiRequest("/api/downloads","POST",{device:m.device,path:m.path}));}catch(err){status.textContent=err.message;button.disabled=false;}});
 try{show((await apiRequest("/api/downloads")).find(x=>x.device===m.device&&x.path===m.path));}catch(_){show(null);}
}
function details(m){$("detail-title").textContent=m.name;const dl=$("detail-fields");dl.replaceChildren();for(const [k,v] of Object.entries({Type:m.kind,Device:m.device,Size:fmt(m.size_bytes),Family:(m.family_guess||"Unknown")+(m.family_verified?" (from source)":" (unverified)"),...(m.source?{Source:providerName(m.source.provider)+" · "+m.source.model_name+(m.source.version_name?" — "+m.source.version_name:"")}:{"Source":m.sha256?"Not found on Civitai":"Not identified yet"}),...(m.source&&m.source.base_model?{"Base model":m.source.base_model}:{}),...(m.source&&m.source.trained_words&&m.source.trained_words.length?{"Trigger words":m.source.trained_words.join(", ")}:{}),...(m.update?{Update:m.update.version_name}:{}),Duplicates:m.possible_duplicate?"Possible match — verify hashes":"No matching name and size",Folder:m.path.replace(/[\\/][^\\/]+$/,""),Path:m.path,...(selectedMatches.some(s=>s.device===m.device&&s.path===m.path)?{"Verified source":selectedMatches.filter(s=>s.device===m.device&&s.path===m.path).map(s=>"Hugging Face · "+s.repository+" / "+s.remote_path).join(", ")}:{})})){dl.append(text("dt",k),text("dd",v));}if(m.source)linkRow(dl,"Source page","Open on "+providerName(m.source.provider)+" ↗",m.source.url);if(m.update)linkRow(dl,"New version","Open "+m.update.version_name+" ↗",m.update.url);$("provider-query").value=m.name.replace(/\.(safetensors|ckpt|gguf|pt|pth|bin)$/i,"").replace(/[_-]+/g," ").slice(0,120);$("provider-results").replaceChildren();$("repo-files").replaceChildren();$("repo-status").textContent="";$("repo-filter").value="";renderDownload(m);$("detail-dialog").showModal();}
function options(id,values,label){const el=$(id),old=el.value;el.replaceChildren(new Option(label,""));values.forEach(v=>el.add(new Option(v,v)));el.value=values.includes(old)?old:"";}
function render(){
 $("count").textContent=models.length.toLocaleString();
 $("size").textContent=fmt(models.reduce((n,m)=>n+(m.hard_link?0:Number(m.size_bytes)||0),0));
 const devices=[...new Set(models.map(m=>m.device))].sort();
 const physical=[...new Set(models.map(m=>m.physical_device||m.device))];
 $("devices-count").textContent=physical.length;
 const files=models.filter(m=>!m.hard_link);
 $("updates-count").textContent=files.filter(m=>m.update).length.toLocaleString();
 $("identified-count").textContent=files.filter(m=>m.source).length+" of "+files.length+" files identified";
 fill($("recent"),models.slice(0,8),"No models indexed yet. Run the Windows inventory agent.");
 const counts=new Map();models.forEach(m=>counts.set(m.kind,(counts.get(m.kind)||0)+1));
 $("types").replaceChildren();[...counts].sort((a,b)=>b[1]-a[1]).slice(0,10).forEach(([kind,count])=>{const row=text("div");const line=text("div","","type-line");line.append(text("span",kind),text("span",String(count)));const bar=text("div","","bar");const inner=text("span");inner.style.width=(count/Math.max(models.length,1)*100)+"%";bar.append(inner);row.append(line,bar);$("types").append(row);});
 if(!counts.size)$("types").append(text("p","Model categories will appear after the first inventory scan.","muted"));
 options("kind",[...counts.keys()].sort(),"All types");options("device",devices,"All sources");options("family",[...new Set(models.map(m=>m.family_guess||"Unknown"))].sort(),"All families");options("folder",[...new Set(models.filter(m=>m.kind==="lora").map(m=>m.path.replace(/[\\/][^\\/]+$/,"")))].sort(),"All LoRA folders");
 const deviceList=$("device-list");deviceList.replaceChildren();
 agents.forEach(a=>{const card=text("div","","agent-card");const state=text("span",a.online?"● Online":"● Offline",a.online?"agent-online":"agent-offline");card.append(text("strong",a.device),state,text("div","ComfyUI: "+a.comfyui_count+" · Ollama: "+(a.ollama_count ?? "unavailable"),"model-sub"),text("div","Last sync: "+new Date(a.last_seen*1000).toLocaleString(),"model-sub"));deviceList.append(card);});
 devices.forEach(device=>{const matching=models.filter(m=>m.device===device);const b=text("button","","model");b.append(text("span","▣","glyph"));const info=text("span","","model-text");info.append(text("div",device,"model-name"),text("div",matching.length+" models · "+fmt(matching.reduce((n,m)=>n+(m.hard_link?0:m.size_bytes),0)),"model-sub"));b.append(info,text("span","›","chevron"));b.onclick=()=>{switchView("library");$("device").value=device;filter();};deviceList.append(b);});
 if(!devices.length)deviceList.append(text("p","No inventory sources registered.","muted"));
 filter();
}
function filter(){
 const q=$("search").value.trim().toLowerCase(),kind=$("kind").value,device=$("device").value;
 const family=$("family").value, folder=$("folder").value, duplicates=$("duplicates").checked, updates=$("updates").checked, sort=$("sort").value;
 const subset=models.filter(m=>(!q||(m.name+" "+m.path).toLowerCase().includes(q))&&(!kind||m.kind===kind)&&(!device||m.device===device)&&(!family||m.family_guess===family)&&(!folder||m.kind==="lora"&&m.path.replace(/[\\/][^\\/]+$/,"")===folder)&&(!duplicates||m.possible_duplicate)&&(!updates||m.update));
 subset.sort((a,b)=>sort==="size-desc"?b.size_bytes-a.size_bytes:sort==="size-asc"?a.size_bytes-b.size_bytes:sort==="kind"?a.kind.localeCompare(b.kind)||a.name.localeCompare(b.name):a.name.localeCompare(b.name));
 $("results").textContent=subset.length+" results";
 fill($("library"),subset.slice(0,500),"No matching models.");
 if(subset.length>500)$("library").append(text("p","Showing first 500 results. Narrow your filters to see more.","muted"));
}
async function refresh(){if(!token){openAuth();return;} $("refresh").disabled=true;try{const response=await fetch("/api/models",{headers:{"X-ModelHarbor-Token":token},cache:"no-store"});if(response.status===401){token="";sessionStorage.removeItem("modelharbor_token");setStatus(false);showNotice("Token rejected. Enter your configured API token.");openAuth();return;}if(!response.ok)throw Error("Server returned HTTP "+response.status);const data=await response.json();if(!Array.isArray(data))throw Error("Unexpected inventory response");models=data;try{selectedMatches=await apiRequest("/api/selected-matches");}catch(_){selectedMatches=[];}try{const agentsResponse=await fetch("/api/agents",{headers:{"X-ModelHarbor-Token":token},cache:"no-store"});agents=agentsResponse.ok?await agentsResponse.json():[];}catch(_){agents=[];}setStatus(true);showNotice("");render();}catch(err){setStatus(false);showNotice("Unable to load inventory: "+err.message);}finally{$("refresh").disabled=false;}}
document.querySelectorAll(".tab").forEach(b=>b.addEventListener("click",()=>switchView(b.dataset.view)));
document.querySelectorAll("[data-go]").forEach(b=>b.addEventListener("click",()=>switchView(b.dataset.go)));
["search","kind","device","family","folder","sort","duplicates","updates"].forEach(id=>$(id).addEventListener(id==="search"?"input":"change",filter));
$("refresh").onclick=refresh;$("settings").onclick=openAuth;
$("auth-form").addEventListener("submit",e=>{e.preventDefault();token=$("token").value.trim();sessionStorage.setItem("modelharbor_token",token);$("auth-dialog").close();refresh();});
$("cancel-auth").onclick=()=>$("auth-dialog").close();
$("close-detail").onclick=$("detail-done").onclick=()=>$("detail-dialog").close();
if(token)refresh();else openAuth();
})();
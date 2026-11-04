// shared helpers
const WD = ["日","一","二","三","四","五","六"];
function taipeiToday(){return new Intl.DateTimeFormat("sv-SE",{timeZone:"Asia/Taipei"}).format(new Date());}
function fmtDate(iso){const [y,m,d]=iso.split("-").map(Number);const w=new Date(Date.UTC(y,m-1,d)).getUTCDay();return `${y}/${String(m).padStart(2,"0")}/${String(d).padStart(2,"0")}（${WD[w]}）`;}
function el(tag,attrs={},...kids){const e=document.createElement(tag);for(const[k,v]of Object.entries(attrs)){if(k==="class")e.className=v;else if(k==="text")e.textContent=v;else if(v!==false&&v!=null)e.setAttribute(k,v===true?"":v);}for(const kid of kids.flat()){if(kid==null)continue;e.append(kid.nodeType?kid:document.createTextNode(kid));}return e;}
async function loadConfs(){const r=await fetch("data/conferences.json",{cache:"no-cache"});if(!r.ok)throw new Error("data "+r.status);return r.json();}
function trLabel(c){if(c.transcript_status==="done")return["逐字稿：已完成","tr-done"];if(c.transcript_status==="processing")return["逐字稿：轉換中","tr-pending"];if(c.transcript_status==="pending")return["逐字稿：待轉","tr-pending"];return["逐字稿：尚無影片",""];}
function badges(c){const b=el("div",{class:"badges"});if(c.has_video)b.append(el("span",{class:"b vid",text:c.video&&c.video.offsite?"有影片 (站外)":"有影片"}));if(c.has_slides)b.append(el("span",{class:"b sld",text:"有簡報"}));if(c.transcript_status!=="none"){const[t,k]=trLabel(c);b.append(el("span",{class:"b "+k,text:t}));}else if(!c.held)b.append(el("span",{class:"b",text:"尚未舉行"}));else if(!c.has_video)b.append(el("span",{class:"b",text:"逐字稿：尚無影片"}));return b;}

"""
web_editor.py — G.I.L. website customizer.

Injects a self-contained design studio into every generated site, so the
user can make it theirs without touching code:

  • Edit text     — toggle inline editing, click any text and retype it
  • Brand colors  — accent / secondary / background pickers (drive the
                    site's :root CSS variables live)
  • Fonts         — heading + body font choices (Google Fonts, loaded live)
  • Images        — click-to-replace any image (or hero background) with
                    the user's own file, embedded as base64 so it survives
                    saving and sharing
  • Save          — writes a clean copy of the customized page (File System
                    Access API when available, download fallback)

Everything is vanilla JS in one block — no dependencies, works offline,
survives in the saved file so the site stays editable later.
"""

_EDITOR_BLOCK = r"""
<!-- G.I.L. Customizer -->
<style id="gil-ed-css">
#gil-ed{position:fixed;right:18px;bottom:18px;z-index:2147483000;font-family:'Segoe UI',system-ui,sans-serif;}
#gil-ed *{box-sizing:border-box;}
#gil-ed-fab{display:flex;align-items:center;gap:8px;background:#10102A;color:#3FDDFA;
border:1px solid #3FDDFA;border-radius:30px;padding:11px 20px;cursor:pointer;font-size:14px;
font-weight:600;box-shadow:0 4px 24px rgba(0,0,0,.45);user-select:none;}
#gil-ed-fab:hover{background:#181446;}
#gil-ed-panel{display:none;position:absolute;right:0;bottom:56px;width:280px;background:#0E0C22;
border:1px solid #2A2454;border-radius:16px;padding:16px;color:#EDE9FF;
box-shadow:0 12px 48px rgba(0,0,0,.6);}
#gil-ed-panel h4{margin:0 0 4px;font-size:13px;color:#3FDDFA;font-weight:700;}
#gil-ed-panel .gil-sec{margin:12px 0 0;padding-top:10px;border-top:1px solid #1E1840;}
#gil-ed-panel label{display:block;font-size:11px;color:#8A7AAA;margin:7px 0 3px;}
#gil-ed-panel select,#gil-ed-panel input[type=color]{width:100%;background:#16123A;color:#EDE9FF;
border:1px solid #2A2454;border-radius:8px;padding:6px;font-size:12px;height:32px;cursor:pointer;}
.gil-btn{width:100%;background:#16123A;color:#EDE9FF;border:1px solid #2A2454;border-radius:8px;
padding:8px;font-size:12px;cursor:pointer;margin-top:6px;font-weight:600;}
.gil-btn:hover{border-color:#3FDDFA;color:#3FDDFA;}
.gil-btn.gil-on{background:#0B3A46;border-color:#3FDDFA;color:#3FDDFA;}
.gil-btn.gil-save{background:#3FDDFA;color:#04101A;border:none;margin-top:12px;}
.gil-hint{font-size:10px;color:#665A8C;margin-top:4px;line-height:1.4;}
body.gil-imgmode img,body.gil-imgmode [data-gil-bg]{outline:2px dashed #3FDDFA;outline-offset:2px;cursor:crosshair!important;}
</style>
<div id="gil-ed" contenteditable="false">
  <div id="gil-ed-panel">
    <h4>◈ G.I.L. Design Studio</h4>
    <div class="gil-hint">Make this site yours — changes apply instantly.</div>
    <div class="gil-sec">
      <button class="gil-btn" id="gil-ed-text">✎ Edit text: OFF</button>
      <div class="gil-hint">When ON, click any text on the page and type.</div>
    </div>
    <div class="gil-sec">
      <label>Brand color</label><input type="color" id="gil-ed-c1" value="#f4a261">
      <label>Secondary color</label><input type="color" id="gil-ed-c2" value="#e76f51">
      <label>Background</label><input type="color" id="gil-ed-bg" value="#0c0a08">
    </div>
    <div class="gil-sec">
      <label>Heading font</label>
      <select id="gil-ed-fd"></select>
      <label>Body font</label>
      <select id="gil-ed-fb"></select>
    </div>
    <div class="gil-sec">
      <button class="gil-btn" id="gil-ed-img">🖼 Replace images: OFF</button>
      <div class="gil-hint">When ON, click any photo to swap in your own.</div>
    </div>
    <button class="gil-btn gil-save" id="gil-ed-save">💾 Save my website</button>
  </div>
  <div id="gil-ed-fab">✎ Customize</div>
</div>
<script id="gil-ed-js">
(function(){
"use strict";
var D=document, R=D.documentElement, $=function(id){return D.getElementById(id);};
var panel=$('gil-ed-panel'), fab=$('gil-ed-fab');
fab.addEventListener('click',function(){panel.style.display=panel.style.display==='block'?'none':'block';});

/* ---- helpers ---- */
function setVar(n,v){R.style.setProperty(n,v);}
function hexToRgb(h){h=h.replace('#','');if(h.length===3)h=h.split('').map(function(c){return c+c;}).join('');
var n=parseInt(h,16);return ((n>>16)&255)+','+((n>>8)&255)+','+(n&255);}
function curVar(n){return getComputedStyle(R).getPropertyValue(n).trim();}
function toHex(c){var m=c.match(/\d+/g);if(!m)return c;
return '#'+m.slice(0,3).map(function(x){return (+x).toString(16).padStart(2,'0');}).join('');}

/* ---- init color pickers from the site's real palette ---- */
try{
var c1=curVar('--accent'),c2=curVar('--accent2'),bg=curVar('--bg');
if(c1)$('gil-ed-c1').value=c1[0]==='#'?c1:toHex(c1);
if(c2)$('gil-ed-c2').value=c2[0]==='#'?c2:toHex(c2);
if(bg)$('gil-ed-bg').value=bg[0]==='#'?bg:toHex(bg);
}catch(e){}
$('gil-ed-c1').addEventListener('input',function(){setVar('--accent',this.value);setVar('--accent-rgb',hexToRgb(this.value));});
$('gil-ed-c2').addEventListener('input',function(){setVar('--accent2',this.value);});
$('gil-ed-bg').addEventListener('input',function(){setVar('--bg',this.value);});

/* ---- fonts ---- */
var FONTS=[['Playfair Display','serif'],['DM Serif Display','serif'],['Cormorant Garamond','serif'],
['Bebas Neue','sans-serif'],['Space Grotesk','sans-serif'],['Montserrat','sans-serif'],
['Poppins','sans-serif'],['Inter','sans-serif'],['Lato','sans-serif'],['Plus Jakarta Sans','sans-serif']];
[['gil-ed-fd','--font-d'],['gil-ed-fb','--font-b']].forEach(function(cfg){
  var sel=$(cfg[0]);
  sel.innerHTML='<option value="">(keep current)</option>'+FONTS.map(function(f){
    return '<option value="'+f[0]+'|'+f[1]+'">'+f[0]+'</option>';}).join('');
  sel.addEventListener('change',function(){
    if(!this.value)return;
    var p=this.value.split('|'),fam=p[0],fb=p[1];
    var id='gil-font-'+fam.replace(/ /g,'-');
    if(!$(id)){var l=D.createElement('link');l.id=id;l.rel='stylesheet';
      l.href='https://fonts.googleapis.com/css2?family='+fam.replace(/ /g,'+')+':wght@400;600;700;800&display=swap';
      D.head.appendChild(l);}
    setVar(cfg[1],"'"+fam+"',"+fb);
  });
});

/* ---- edit text ---- */
var tBtn=$('gil-ed-text'),editing=false;
tBtn.addEventListener('click',function(){
  editing=!editing;
  D.body.contentEditable=editing?'true':'false';
  $('gil-ed').contentEditable='false';
  tBtn.textContent='✎ Edit text: '+(editing?'ON':'OFF');
  tBtn.classList.toggle('gil-on',editing);
});

/* ---- replace images ---- */
var iBtn=$('gil-ed-img'),imgMode=false,pendTarget=null;
var fileIn=D.createElement('input');fileIn.type='file';fileIn.accept='image/*';fileIn.style.display='none';
D.body.appendChild(fileIn);
/* mark elements that use a CSS background image (e.g. the hero) */
Array.prototype.forEach.call(D.querySelectorAll('header,section,div'),function(el){
  if(el.closest('#gil-ed'))return;
  var b=getComputedStyle(el).backgroundImage;
  if(b&&b.indexOf('url(')>-1)el.setAttribute('data-gil-bg','1');
});
iBtn.addEventListener('click',function(){
  imgMode=!imgMode;
  iBtn.textContent='🖼 Replace images: '+(imgMode?'ON':'OFF');
  iBtn.classList.toggle('gil-on',imgMode);
  D.body.classList.toggle('gil-imgmode',imgMode);
});
D.addEventListener('click',function(e){
  if(!imgMode)return;
  if(e.target.closest('#gil-ed'))return;
  var img=e.target.closest('img'), bgEl=e.target.closest('[data-gil-bg]');
  if(!img&&!bgEl)return;
  e.preventDefault();e.stopPropagation();
  pendTarget=img||bgEl;
  fileIn.click();
},true);
fileIn.addEventListener('change',function(){
  var f=this.files[0];if(!f||!pendTarget)return;
  var rd=new FileReader();
  rd.onload=function(){
    var url=rd.result;
    if(pendTarget.tagName==='IMG'){pendTarget.src=url;}
    else{var b=getComputedStyle(pendTarget).backgroundImage;
      pendTarget.style.backgroundImage=b.replace(/url\([^)]*\)/,'url("'+url+'")');}
    pendTarget=null;fileIn.value='';
  };
  rd.readAsDataURL(f);
});

/* ---- save ---- */
$('gil-ed-save').addEventListener('click',function(){
  D.body.contentEditable='false';editing=false;
  tBtn.textContent='✎ Edit text: OFF';tBtn.classList.remove('gil-on');
  D.body.classList.remove('gil-imgmode');
  /* bake current CSS-var overrides into the saved copy */
  var vars='';
  ['--accent','--accent2','--accent-rgb','--bg','--font-d','--font-b'].forEach(function(v){
    var val=R.style.getPropertyValue(v);if(val)vars+=v+':'+val+';';});
  var bakeId='gil-baked-vars',bake=$(bakeId);
  if(vars){if(!bake){bake=D.createElement('style');bake.id=bakeId;D.head.appendChild(bake);}
    bake.textContent=':root{'+vars+'}';}
  var html='<!DOCTYPE html>\n'+R.outerHTML;
  function fallback(){
    var a=D.createElement('a');
    a.href=URL.createObjectURL(new Blob([html],{type:'text/html'}));
    a.download='index.html';a.click();
    alert('Saved! Your customized site downloaded as index.html - replace the old file with it.');
  }
  if(window.showSaveFilePicker){
    window.showSaveFilePicker({suggestedName:'index.html',
      types:[{description:'Website',accept:{'text/html':['.html']}}]})
    .then(function(h){return h.createWritable();})
    .then(function(w){return w.write(html).then(function(){return w.close();});})
    .then(function(){alert('Saved! Your website is updated.');})
    .catch(function(err){if(err&&err.name!=='AbortError')fallback();});
  }else{fallback();}
});
})();
</script>
"""


def inject_editor(html: str) -> str:
    """Insert the customizer overlay right before </body> (append if missing).
    Idempotent — never injects twice."""
    if 'id="gil-ed-js"' in html:
        return html
    import re as _re
    m = _re.search(r"</body\s*>", html, _re.IGNORECASE)
    if m:
        return html[:m.start()] + _EDITOR_BLOCK + html[m.start():]
    return html + _EDITOR_BLOCK

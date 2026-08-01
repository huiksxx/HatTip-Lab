(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const elements = {
    form: $("settings-form"), provider: $("provider"), providerList: $("provider-list"),
    providerHermes: $("provider-hermes"), providerOpenai: $("provider-openai"), providerHttp: $("provider-http"),
    openaiKey: $("openai-api-key"), openaiKeyState: $("openai-key-state"), clearOpenaiKey: $("clear-openai-key"),
    openaiModel: $("openai-model"), openaiBaseUrl: $("openai-base-url"), httpEndpoint: $("http-endpoint"),
    httpModel: $("http-model"), httpKey: $("http-api-key"), httpKeyState: $("http-key-state"),
    clearHttpKey: $("clear-http-key"), temperature: $("temperature"), maxTokens: $("max-tokens"),
    mode: $("mode"), modelId: $("model-id"), modelGrid: $("model-grid"), importModel: $("import-model"),
    installCore: $("install-core"), modelGuide: $("model-guide"), coreState: $("core-state"),
    voiceInputEnabled: $("voice-input-enabled"), hotkey: $("push-to-talk-hotkey"), recordHotkey: $("record-hotkey"),
    ttsEnabled: $("tts-enabled"), ttsEngine: $("tts-engine"), ttsAvailability: $("tts-availability"),
    ttsEngineHelp: $("tts-engine-help"), edgeDot: $("edge-dot"), edgeState: $("edge-state"), edgeVoice: $("edge-voice"),
    piperDot: $("piper-dot"), piperState: $("piper-state"), sovitsDot: $("sovits-dot"), sovitsState: $("sovits-state"),
    importPiper: $("import-piper"), installSovits: $("install-sovits"), installProgress: $("install-progress"),
    installProgressBar: $("install-progress-bar"), installProgressText: $("install-progress-text"),
    sovitsUrl: $("gpt-sovits-url"), sovitsVoice: $("gpt-sovits-voice"), importVoice: $("import-voice"),
    sovitsPrompt: $("gpt-sovits-prompt"), scale: $("scale"), onTop: $("on-top"),
    idleAnimations: $("idle-animations"), minimizeToTray: $("minimize-to-tray"),
    save: $("save"), close: $("close"), message: $("message"), saveState: $("save-state"),
  };

  let api;
  let current = {};
  let recordingHotkey = false;
  let installPoll = null;

  function fallbackApi() {
    const base = { ok:true, provider:"hermes", providers:[{id:"hermes",name:"Hermes CLI",available:true}], models:[], mode:"gif", scale:1, on_top:true, openai_model:"gpt-4o-mini", openai_base_url:"https://api.openai.com/v1", http_endpoint:"", http_model:"", temperature:.8, max_tokens:1200, voice_input_enabled:true, push_to_talk_hotkey:"Alt+Space", tts_enabled:false, tts_engine:"edge", tts_voice:"zh-CN-XiaoxiaoNeural", tts_available:true, tts_status:{edge_available:true,piper_available:false,gpt_sovits_online:false}, voice_profiles:[], gpt_sovits_url:"", idle_animations:true, minimize_to_tray:true, sovits_install:{installed:false,running:false} };
    return {
      async get_settings(){ return base; }, async save_settings(values){ return {...base,...values}; },
      async import_live2d_model(){ return {ok:false,error:"请在桌面程序中导入模型"}; }, async install_live2d_core(){ return {ok:false,error:"请在桌面程序中安装 Core"}; },
      async import_piper_model(){ return {ok:false,error:"请在桌面程序中导入"}; }, async import_voice_profile(){ return {ok:false,error:"请在桌面程序中导入"}; },
      async install_sovits(){ return {ok:false,error:"请在桌面程序中安装"}; }, async get_sovits_install_status(){ return {ok:true,installed:false,running:false}; },
      async open_model_guide(){ return {ok:true}; }, async close_settings(){ return {ok:true}; },
    };
  }

  function connectApi() {
    if (window.pywebview?.api) return Promise.resolve(window.pywebview.api);
    return new Promise((resolve) => {
      const timer = setTimeout(() => resolve(fallbackApi()), 900);
      window.addEventListener("pywebviewready", () => { clearTimeout(timer); resolve(window.pywebview.api); }, {once:true});
    });
  }

  function setMessage(text = "", kind = "") { elements.message.textContent = text; elements.message.className = kind; }
  function setDirty(dirty = true) { elements.saveState.textContent = dirty ? "有未保存更改" : "已同步"; elements.saveState.classList.toggle("dirty", dirty); }

  function selectTab(name) {
    document.querySelectorAll(".tab-button").forEach((button) => {
      const active = button.dataset.tab === name; button.classList.toggle("active", active); button.setAttribute("aria-selected", String(active));
    });
    document.querySelectorAll(".tab-panel").forEach((panel) => { const active = panel.dataset.panel === name; panel.hidden = !active; panel.classList.toggle("active", active); });
  }

  function showProviderPanel() {
    const provider = elements.provider.value;
    elements.providerHermes.hidden = provider !== "hermes" && provider !== "mock";
    elements.providerOpenai.hidden = provider !== "openai";
    elements.providerHttp.hidden = provider !== "http";
    elements.providerList.querySelectorAll("button").forEach((button) => button.classList.toggle("selected", button.dataset.provider === provider));
  }

  function rebuildProviders(data) {
    elements.providerList.replaceChildren(); elements.provider.value = data.provider || "hermes";
    for (const provider of data.providers || []) {
      if (provider.id === "mock") continue;
      const button = document.createElement("button"); button.type = "button"; button.className = "provider-choice"; button.dataset.provider = provider.id;
      const dot = document.createElement("span"); dot.className = `status-light${provider.available ? " ready" : ""}`;
      const label = document.createElement("span"); label.textContent = provider.name;
      button.append(dot, label); button.addEventListener("click", () => { elements.provider.value = provider.id; showProviderPanel(); setDirty(true); });
      elements.providerList.appendChild(button);
    }
    showProviderPanel();
  }

  function rebuildModels(data) {
    elements.modelId.replaceChildren(); elements.modelGrid.replaceChildren();
    if (!(data.models || []).length) {
      const option = document.createElement("option"); option.value = ""; elements.modelId.appendChild(option);
      const empty = document.createElement("div"); empty.className = "empty-card"; empty.textContent = "尚未导入 Live2D 模型；GIF 模式仍可正常使用。"; elements.modelGrid.appendChild(empty); return;
    }
    for (const model of data.models) {
      const option = document.createElement("option"); option.value = model.id; option.textContent = model.name; option.selected = model.id === data.model_id; elements.modelId.appendChild(option);
      const card = document.createElement("button"); card.type = "button"; card.className = `model-card${model.id === data.model_id ? " selected" : ""}`; card.dataset.model = model.id;
      const thumb = document.createElement("span"); thumb.className = "model-thumb";
      if (model.thumbnail_url) { const image = document.createElement("img"); image.src = model.thumbnail_url; image.alt = ""; image.loading = "lazy"; thumb.appendChild(image); } else { thumb.textContent = "◇"; }
      const name = document.createElement("strong"); name.textContent = model.name; card.append(thumb, name);
      card.addEventListener("click", () => { elements.modelId.value = model.id; elements.modelGrid.querySelectorAll("button").forEach((item) => item.classList.toggle("selected", item === card)); setDirty(true); });
      elements.modelGrid.appendChild(card);
    }
  }

  function rebuildVoices(data) {
    elements.sovitsVoice.replaceChildren();
    const empty = document.createElement("option"); empty.value = ""; empty.textContent = "尚未选择参考音频"; elements.sovitsVoice.appendChild(empty);
    for (const voice of data.voice_profiles || []) { const option = document.createElement("option"); option.value = voice.id; option.textContent = voice.name; option.selected = voice.id === data.gpt_sovits_voice; elements.sovitsVoice.appendChild(option); }
  }

  function renderInstall(status = {}) {
    const active = Boolean(status.stage && status.progress < 100 && status.stage !== "安装失败") || Boolean(status.running && !status.installed);
    elements.installProgress.hidden = !(active || status.stage === "安装失败");
    elements.installProgressBar.style.width = `${Math.max(0, Math.min(Number(status.progress || 0), 100))}%`;
    elements.installProgressText.textContent = [status.stage, status.message].filter(Boolean).join(" · ");
    elements.installSovits.disabled = active;
    elements.installSovits.textContent = status.installed ? (status.running ? "GPT‑SoVITS 已运行" : "启动 GPT‑SoVITS") : (active ? "正在安装…" : "一键安装 GPT‑SoVITS");
  }

  function normalizeTtsEngine(value) {
    if (value === "auto") return "gpt-sovits";
    return ["edge", "piper", "gpt-sovits"].includes(value) ? value : "edge";
  }

  function renderTtsEngine(engine = elements.ttsEngine.value) {
    const selected = normalizeTtsEngine(engine);
    elements.ttsEngine.value = selected;
    const help = {
      edge: "推荐给大多数用户：无需下载语音模型，保持联网即可朗读；断网时会尝试已配置的本地引擎。",
      piper: "完全在本机合成，隐私和响应速度更好；需要先导入有权使用的 .onnx 模型，否则自动改用 Edge。",
      "gpt-sovits": "适合追求角色感情和自定义音色；需要独立安装并配置参考音频，失败时依次回退到 Piper、Edge。",
    };
    elements.ttsEngineHelp.textContent = help[selected];
    document.querySelectorAll(".engine-card[data-engine]").forEach((card) => card.classList.toggle("selected", card.dataset.engine === selected));
  }

  function render(data) {
    current = data; rebuildProviders(data); rebuildModels(data); rebuildVoices(data);
    elements.openaiModel.value = data.openai_model || "gpt-4o-mini"; elements.openaiBaseUrl.value = data.openai_base_url || "https://api.openai.com/v1";
    elements.httpEndpoint.value = data.http_endpoint || ""; elements.httpModel.value = data.http_model || ""; elements.temperature.value = data.temperature ?? .8; elements.maxTokens.value = data.max_tokens ?? 1200;
    elements.mode.value = data.mode || "gif"; elements.voiceInputEnabled.checked = Boolean(data.voice_input_enabled); elements.hotkey.value = data.push_to_talk_hotkey || "Alt+Space";
    elements.openaiKeyState.textContent = data.openai_api_key_configured ? "已安全保存" : "未配置"; elements.httpKeyState.textContent = data.http_api_key_configured ? "已安全保存" : "可选";
    elements.coreState.className = `notice${data.live2d_runtime_available ? " success" : ""}`; elements.coreState.textContent = data.live2d_runtime_available ? "Cubism Core 已安装在本地用户目录。" : "Cubism Core 未安装；使用 Live2D 前请从合法来源自行安装。";
    elements.ttsEnabled.checked = Boolean(data.tts_enabled); elements.ttsEngine.value = normalizeTtsEngine(data.tts_engine); elements.edgeVoice.value = data.tts_voice || "zh-CN-XiaoxiaoNeural"; elements.sovitsUrl.value = data.gpt_sovits_url || ""; elements.sovitsPrompt.value = data.gpt_sovits_prompt_text || "";
    const tts = data.tts_status || {}; elements.edgeDot.classList.toggle("ready", Boolean(tts.edge_available)); elements.piperDot.classList.toggle("ready", Boolean(tts.piper_available)); elements.sovitsDot.classList.toggle("ready", Boolean(tts.gpt_sovits_online));
    elements.edgeState.textContent = tts.edge_available ? "已内置，联网即可使用" : "组件缺失";
    elements.piperState.textContent = tts.piper_available ? `可用${tts.piper_model ? ` · ${tts.piper_model}` : ""}` : "未导入授权明确的模型";
    elements.sovitsState.textContent = tts.gpt_sovits_online ? "服务在线" : (data.sovits_install?.installed ? "已安装，服务未在线" : "可选，尚未安装");
    elements.ttsAvailability.textContent = tts.edge_available ? "Edge 在线语音已就绪，无需模型" : (tts.piper_available ? "离线 Piper 已就绪" : (tts.gpt_sovits_online ? "GPT‑SoVITS 已在线" : "语音不可用时仍会保留文字回复"));
    renderTtsEngine(elements.ttsEngine.value);
    elements.scale.value = String(data.scale || 1); elements.onTop.checked = Boolean(data.on_top); elements.idleAnimations.checked = data.idle_animations !== false; elements.minimizeToTray.checked = data.minimize_to_tray !== false;
    renderInstall(data.sovits_install || {}); setDirty(false);
  }

  function collect() {
    return { provider:elements.provider.value, openai_api_key:elements.openaiKey.value, clear_openai_api_key:elements.clearOpenaiKey.checked, openai_model:elements.openaiModel.value, openai_base_url:elements.openaiBaseUrl.value,
      http_endpoint:elements.httpEndpoint.value, http_model:elements.httpModel.value, http_api_key:elements.httpKey.value, clear_http_api_key:elements.clearHttpKey.checked,
      temperature:Number(elements.temperature.value), max_tokens:Number(elements.maxTokens.value), mode:elements.mode.value, model_id:elements.modelId.value || null,
      voice_input_enabled:elements.voiceInputEnabled.checked, push_to_talk_hotkey:elements.hotkey.value, tts_enabled:elements.ttsEnabled.checked, tts_engine:elements.ttsEngine.value, tts_voice:elements.edgeVoice.value,
      piper_model:current.piper_model || "", gpt_sovits_url:elements.sovitsUrl.value, gpt_sovits_voice:elements.sovitsVoice.value, gpt_sovits_prompt_text:elements.sovitsPrompt.value,
      gpt_sovits_prompt_lang:"zh", gpt_sovits_text_lang:"zh", scale:Number(elements.scale.value), on_top:elements.onTop.checked, idle_animations:elements.idleAnimations.checked, minimize_to_tray:elements.minimizeToTray.checked };
  }

  async function load() { setMessage("正在读取设置…"); const result = await api.get_settings(); if (!result?.ok) throw new Error(result?.error || "无法读取设置"); render(result); setMessage(""); }
  async function save(event) { event.preventDefault(); elements.save.disabled = true; setMessage("正在安全保存…"); try { const result = await api.save_settings(collect()); if (!result?.ok) throw new Error(result?.error || "保存失败"); elements.openaiKey.value = ""; elements.httpKey.value = ""; elements.clearOpenaiKey.checked = false; elements.clearHttpKey.checked = false; render(result); setMessage("设置已保存并立即生效。","success"); } catch(error){ setMessage(error.message || String(error),"error"); } finally { elements.save.disabled = false; } }

  function normalizeHotkey(event) { const modifiers=[]; if(event.ctrlKey)modifiers.push("Ctrl"); if(event.altKey)modifiers.push("Alt"); if(event.shiftKey)modifiers.push("Shift"); if(event.metaKey)modifiers.push("Win"); if(["Control","Alt","Shift","Meta"].includes(event.key))return null; const key=event.code==="Space"?"Space":(/^Key[A-Z]$/.test(event.code)?event.code.slice(3):(/^F\d+$/.test(event.key)?event.key:null)); return modifiers.length&&key?[...modifiers,key].join("+"):null; }
  function startHotkeyCapture(){ recordingHotkey=true; elements.recordHotkey.textContent="请按组合键…"; elements.hotkey.value=""; elements.hotkey.focus(); }
  async function runAction(action, pending, success) { setMessage(pending); const result = await action(); if(result?.cancelled)return setMessage("已取消。"); if(!result?.ok)return setMessage(result?.error || "操作失败","error"); await load(); setMessage(success(result),"success"); }
  async function installSovits(){ if(current.sovits_install?.installed){ const result=await api.start_sovits?.(); if(!result?.ok)return setMessage(result?.error || "启动失败","error"); await load(); return; } if(!window.confirm("将下载 GPT‑SoVITS、依赖和模型到用户目录，通常需要 5–10 分钟且占用数 GB。继续吗？"))return; const result=await api.install_sovits(); if(!result?.ok)return setMessage(result?.error || "无法开始安装","error"); renderInstall({...result,stage:"准备安装",progress:1}); installPoll=window.setInterval(async()=>{ const status=await api.get_sovits_install_status(); if(status?.ok){ renderInstall(status); if(status.installed || status.stage==="安装失败"){ clearInterval(installPoll); installPoll=null; await load(); } } },1800); }

  async function init() {
    api=await connectApi(); await load();
    document.querySelectorAll(".tab-button").forEach((button)=>button.addEventListener("click",()=>selectTab(button.dataset.tab)));
    elements.form.addEventListener("submit",save); elements.form.addEventListener("input",()=>setDirty(true));
    elements.ttsEngine.addEventListener("change",()=>renderTtsEngine());
    document.querySelectorAll(".reveal").forEach((button)=>button.addEventListener("click",()=>{ const input=$(button.dataset.target); const reveal=input.type==="password"; input.type=reveal?"text":"password"; button.textContent=reveal?"隐藏":"显示"; }));
    elements.recordHotkey.addEventListener("click",startHotkeyCapture);
    window.addEventListener("keydown",(event)=>{ if(!recordingHotkey)return; event.preventDefault(); event.stopPropagation(); if(event.key==="Escape"){recordingHotkey=false;elements.hotkey.value=current.push_to_talk_hotkey||"Alt+Space";elements.recordHotkey.textContent="重新设置";return;} const value=normalizeHotkey(event);if(!value)return;recordingHotkey=false;elements.hotkey.value=value;elements.recordHotkey.textContent="重新设置";setDirty(true);},true);
    elements.importModel.addEventListener("click",()=>runAction(()=>api.import_live2d_model(),"请选择你有权使用的 Live2D 模型…",(result)=>`已导入“${result.model.name}”。`));
    elements.installCore.addEventListener("click",()=>runAction(()=>api.install_live2d_core(),"请选择 Cubism Core 文件…",()=>"Cubism Core 已安装到本地用户目录。"));
    elements.importPiper.addEventListener("click",()=>runAction(()=>api.import_piper_model(),"请选择授权明确的 .onnx 与同名配置文件…",()=>"Piper 模型已导入。"));
    elements.importVoice.addEventListener("click",()=>runAction(()=>api.import_voice_profile(),"请选择你有权使用的 5–15 秒参考音频…",()=>"参考音频已导入。"));
    elements.installSovits.addEventListener("click",installSovits); elements.modelGuide.addEventListener("click",()=>api.open_model_guide()); elements.close.addEventListener("click",()=>api.close_settings());
  }

  window.onSovitsInstallProgress=(payload)=>renderInstall(payload || {});
  window.addEventListener("beforeunload",()=>{ if(installPoll)clearInterval(installPoll); });
  init().catch((error)=>setMessage(error.message || String(error),"error"));
})();

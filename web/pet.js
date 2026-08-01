(() => {
  "use strict";

  const elements = {
    shell: document.getElementById("pet-shell"),
    bubble: document.getElementById("speech-bubble"),
    bubbleText: document.getElementById("bubble-text"),
    bubbleClose: document.getElementById("bubble-close"),
    stage: document.getElementById("character-stage"),
    hitbox: document.getElementById("character-hitbox"),
    gif: document.getElementById("gif-pet"),
    liveHost: document.getElementById("live2d-host"),
    liveCanvas: document.getElementById("live2d-canvas"),
    composer: document.getElementById("composer"),
    input: document.getElementById("message-input"),
    mic: document.getElementById("mic-button"),
    send: document.getElementById("send-button"),
    listening: document.getElementById("listening-indicator"),
    menu: document.getElementById("context-menu"),
    liveMenu: document.getElementById("live2d-menu-item"),
    coreMenu: document.getElementById("install-core-menu-item"),
    providerSelect: document.getElementById("provider-select"),
    modelSelect: document.getElementById("model-select"),
    onTopMenu: document.getElementById("on-top-menu-item"),
    status: document.getElementById("status-dot"),
  };

  const state = {
    config: {
      mode: "gif",
      provider: "mock",
      providers: [],
      models: [],
      model_id: null,
      selected_model: null,
      scale: 1,
      on_top: true,
      live2d_available: false,
      live2d_runtime_available: false,
      live2d_runtime_url: "",
      external_bubble: false,
      voice_input_enabled: true,
      push_to_talk_hotkey: "Alt+Space",
      tts_enabled: false,
      tts_voice: "zh-CN-XiaoxiaoNeural",
      tts_available: false,
      idle_animations: true,
    },
    busy: false,
    api: null,
    live2d: {
      app: null,
      model: null,
      modelId: null,
      loading: null,
      loadSerial: Promise.resolve(),
      loadVersion: 0,
      fit: null,
      fitFrame: null,
      resizeObserver: null,
      contentBounds: null,
      calibrationFrame: null,
    },
    drag: {
      startX: 0,
      startY: 0,
      moved: false,
      tracking: false,
      pointerId: null,
      ready: null,
      latest: null,
      frame: null,
      suppressClickUntil: 0,
    },
    speech: {
      recognition: null,
      listening: false,
      transcript: "",
      interim: "",
      shouldSend: false,
      sendingTranscript: false,
      audio: null,
      mouthFrame: null,
    },
    typingToken: 0,
    idleTimer: null,
    thinkingBlinkFrame: null,
  };

  function browserFallbackApi() {
    return {
      async bootstrap() {
        return {
          mode: "gif",
          provider: "preview",
          providers: [{ id: "preview", name: "浏览器预览", available: true }],
          models: [],
          scale: 1,
          on_top: true,
          live2d_available: false,
          live2d_runtime_available: false,
          external_bubble: false,
          voice_input_enabled: true,
          tts_enabled: false,
          tts_available: false,
          mock: true,
        };
      },
      async chat(text) {
        await new Promise((resolve) => setTimeout(resolve, 650));
        return { ok: true, reply: `浏览器预览收到：“${text}”`, emotion: "happy" };
      },
      async set_provider(provider) { return { ok: true, provider }; },
      async set_mode(mode) { return { ok: mode === "gif", mode, error: "浏览器预览仅支持 GIF" }; },
      async set_model() { return { ok: false, error: "请在桌面程序中导入模型" }; },
      async import_live2d_model() { return { ok: false, error: "请在桌面程序中导入模型" }; },
      async install_live2d_core() { return { ok: false, error: "请在桌面程序中安装 Core" }; },
      async open_model_guide() { return { ok: true }; },
      async open_settings() { return { ok: true }; },
      async synthesize_speech() { return { ok: false, disabled: true }; },
      async report_client_status() { return { ok: true }; },
      async begin_drag() { return { ok: true }; },
      async drag_to() { return { ok: true }; },
      async show_bubble() { return { ok: false }; },
      async hide_bubble() { return { ok: true }; },
      async set_scale(scale) { return { ok: true, scale }; },
      async set_on_top(value) { return { ok: true, on_top: value }; },
      async minimize() { return { ok: true }; },
      async exit_app() { return { ok: true }; },
    };
  }

  async function connectApi() {
    if (window.pywebview && window.pywebview.api) return window.pywebview.api;
    return new Promise((resolve) => {
      const timeout = setTimeout(() => resolve(browserFallbackApi()), 900);
      window.addEventListener("pywebviewready", () => {
        clearTimeout(timeout);
        resolve(window.pywebview.api);
      }, { once: true });
    });
  }

  function setStatus(kind = "ready") {
    elements.status.className = "status-dot";
    if (kind !== "ready") elements.status.classList.add(kind);
  }

  function showLocalBubble(text, { thinking = false, typed = false } = {}) {
    if (thinking) text = "";
    state.typingToken += 1;
    const token = state.typingToken;
    elements.bubble.hidden = false;
    elements.bubble.classList.toggle("thinking", thinking);
    const logicalLines = thinking ? 1 : Math.max(1, Math.ceil(String(text).length / 20));
    elements.bubble.style.minHeight = `${Math.min(150, Math.max(74, 50 + logicalLines * 24))}px`;
    if (!typed) {
      elements.bubbleText.textContent = text;
      return;
    }
    elements.bubbleText.textContent = "";
    let index = 0;
    const step = () => {
      if (token !== state.typingToken) return;
      elements.bubbleText.textContent += text.slice(index, index + 2);
      index += 2;
      if (index < text.length) window.setTimeout(step, 22);
    };
    step();
  }

  function showBubble(text, options = {}) {
    if (state.config.external_bubble && state.api?.show_bubble) {
      state.typingToken += 1;
      elements.bubble.hidden = true;
      Promise.resolve(state.api.show_bubble(String(text), options)).then((result) => {
        if (!result?.ok) showLocalBubble(text, options);
      }).catch(() => showLocalBubble(text, options));
      return;
    }
    showLocalBubble(text, options);
  }

  function hideBubble() {
    state.typingToken += 1;
    elements.bubble.hidden = true;
    if (state.config.external_bubble && state.api?.hide_bubble) {
      Promise.resolve(state.api.hide_bubble()).catch(() => {});
    }
  }

  function setEmotion(emotion, duration = 1200) {
    elements.shell.dataset.emotion = emotion;
    animateLive2d(emotion);
    if (emotion === "thinking") startThinkingBlink();
    else stopThinkingBlink();
    window.clearTimeout(setEmotion.timer);
    if (emotion !== "thinking") {
      setEmotion.timer = window.setTimeout(() => {
        elements.shell.dataset.emotion = "neutral";
      }, duration);
    }
  }

  function openComposer() {
    if (state.busy) return;
    hideBubble();
    elements.composer.hidden = false;
    elements.shell.classList.add("composer-open");
    closeMenu();
    window.requestAnimationFrame(() => {
      scheduleLive2dFit();
      elements.input.focus();
    });
  }

  function closeComposer() {
    if (elements.composer.hidden) return;
    elements.composer.hidden = true;
    elements.shell.classList.remove("composer-open");
    elements.input.blur();
    scheduleLive2dFit();
  }

  function dismissPetUi() {
    closeMenu();
    closeComposer();
    hideBubble();
  }

  function handleCharacterClick() {
    if (performance.now() < state.drag.suppressClickUntil) return false;
    elements.shell.classList.remove("pet-clicked");
    void elements.shell.offsetWidth;
    elements.shell.classList.add("pet-clicked");
    window.setTimeout(() => elements.shell.classList.remove("pet-clicked"), 520);
    markActivity();
    openComposer();
    return true;
  }

  function queueDragPosition(screenX, screenY) {
    state.drag.latest = [screenX, screenY];
    if (state.drag.frame !== null) return;
    state.drag.frame = window.requestAnimationFrame(async () => {
      state.drag.frame = null;
      const point = state.drag.latest;
      state.drag.latest = null;
      if (!point) return;
      try {
        await state.drag.ready;
        await state.api.drag_to(point[0], point[1]);
      } catch (_) {
        // A failed drag must not break click or chat interactions.
      }
      if (state.drag.latest && state.drag.tracking) {
        queueDragPosition(state.drag.latest[0], state.drag.latest[1]);
      }
    });
  }

  function autoSizeInput() {
    elements.input.style.height = "auto";
    elements.input.style.height = `${Math.min(elements.input.scrollHeight, 92)}px`;
  }

  function setBusy(value) {
    state.busy = value;
    elements.input.disabled = value;
    elements.send.disabled = value;
    elements.mic.disabled = value;
    setStatus(value ? "busy" : "ready");
  }

  function setMouthOpen(value) {
    const coreModel = state.live2d.model?.internalModel?.coreModel;
    if (!coreModel) return;
    try {
      if (typeof coreModel.setParameterValueById === "function") {
        coreModel.setParameterValueById("ParamMouthOpenY", value);
      } else if (typeof coreModel.setParamFloat === "function") {
        coreModel.setParamFloat("PARAM_MOUTH_OPEN_Y", value);
      }
    } catch (_) {
      // User models may use a different mouth parameter.
    }
  }

  function stopMouthAnimation() {
    if (state.speech.mouthFrame !== null) cancelAnimationFrame(state.speech.mouthFrame);
    state.speech.mouthFrame = null;
    setMouthOpen(0);
    elements.shell.classList.remove("speaking");
  }

  function startMouthAnimation(audio) {
    stopMouthAnimation();
    elements.shell.classList.add("speaking");
    const started = performance.now();
    const animate = () => {
      if (audio.paused || audio.ended) return stopMouthAnimation();
      const wave = 0.2 + Math.abs(Math.sin((performance.now() - started) / 105)) * 0.72;
      setMouthOpen(wave);
      state.speech.mouthFrame = requestAnimationFrame(animate);
    };
    state.speech.mouthFrame = requestAnimationFrame(animate);
  }

  function stopTts() {
    const audio = state.speech.audio;
    state.speech.audio = null;
    if (audio) {
      audio.pause();
      audio.src = "";
    }
    stopMouthAnimation();
  }

  async function playTts(text, emotion = "neutral") {
    if (!state.config.tts_enabled) return;
    try {
      const result = await state.api.synthesize_speech(text, emotion);
      if (!result?.ok || !result.url) return;
      stopTts();
      const audio = new Audio(result.url);
      state.speech.audio = audio;
      audio.addEventListener("play", () => startMouthAnimation(audio), { once: true });
      audio.addEventListener("ended", stopTts, { once: true });
      audio.addEventListener("error", stopTts, { once: true });
      await audio.play();
    } catch (_) {
      stopTts();
    }
  }

  async function sendMessage(text) {
    if (state.busy) return;
    const normalized = String(text || "").trim();
    if (!normalized) {
      elements.input.focus();
      return;
    }
    stopTts();
    setBusy(true);
    setEmotion("thinking");
    showBubble("...", { thinking: true });
    try {
      const result = await state.api.chat(normalized);
      if (!result || !result.ok) throw new Error(result?.error || "智能体没有回复");
      elements.input.value = "";
      autoSizeInput();
      closeComposer();
      showBubble(result.reply, { typed: true });
      setEmotion(result.emotion || "neutral");
      celebrateMessageArrival();
      void playTts(result.reply, result.emotion || "neutral");
    } catch (error) {
      showBubble(error.message || String(error));
      setEmotion("sad");
      setStatus("error");
    } finally {
      setBusy(false);
    }
  }

  async function submitMessage(event) {
    event.preventDefault();
    await sendMessage(elements.input.value);
  }

  function setListeningUi(active) {
    state.speech.listening = active;
    elements.listening.hidden = !active;
    elements.mic.classList.toggle("listening", active);
    elements.shell.classList.toggle("listening", active);
    if (active) {
      closeComposer();
      hideBubble();
      setEmotion("thinking");
      stopTts();
    } else {
      setEmotion("neutral");
    }
  }

  function ensureRecognition() {
    if (state.speech.recognition) return state.speech.recognition;
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) return null;
    const recognition = new Recognition();
    recognition.lang = "zh-CN";
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.onresult = (event) => {
      let interim = "";
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const value = event.results[index][0]?.transcript || "";
        if (event.results[index].isFinal) state.speech.transcript += value;
        else interim += value;
      }
      state.speech.interim = interim;
    };
    recognition.onerror = (event) => {
      const message = event.error === "not-allowed"
        ? "麦克风权限未开启，请在 Windows 隐私设置中允许桌面应用使用麦克风。"
        : `语音识别失败：${event.error || "未知错误"}`;
      setListeningUi(false);
      showBubble(message, { stay: true });
      setStatus("error");
    };
    recognition.onend = () => {
      setListeningUi(false);
      const transcript = `${state.speech.transcript} ${state.speech.interim}`.trim();
      state.speech.interim = "";
      if (!transcript || state.speech.sendingTranscript) return;
      if (state.speech.shouldSend) {
        state.speech.sendingTranscript = true;
        state.speech.transcript = "";
        Promise.resolve(sendMessage(transcript)).finally(() => {
          state.speech.sendingTranscript = false;
          state.speech.shouldSend = false;
        });
      } else {
        openComposer();
        elements.input.value = transcript;
        autoSizeInput();
      }
    };
    state.speech.recognition = recognition;
    return recognition;
  }

  function startListening() {
    if (state.busy || !state.config.voice_input_enabled) return false;
    const recognition = ensureRecognition();
    if (!recognition) {
      showBubble("当前 WebView2 不支持语音识别，请继续使用文字输入。", { stay: true });
      return false;
    }
    if (state.speech.listening) return true;
    state.speech.transcript = "";
    state.speech.interim = "";
    state.speech.shouldSend = false;
    try {
      recognition.start();
      setListeningUi(true);
      return true;
    } catch (error) {
      showBubble(`无法开始录音：${error.message || error}`);
      return false;
    }
  }

  function stopListening(send = true) {
    state.speech.shouldSend = send;
    if (!state.speech.listening) {
      const transcript = `${state.speech.transcript} ${state.speech.interim}`.trim();
      if (send && transcript && !state.speech.sendingTranscript) {
        state.speech.sendingTranscript = true;
        state.speech.transcript = "";
        Promise.resolve(sendMessage(transcript)).finally(() => {
          state.speech.sendingTranscript = false;
          state.speech.shouldSend = false;
        });
      }
      return;
    }
    try { state.speech.recognition.stop(); } catch (_) { setListeningUi(false); }
  }

  function loadScript(source) {
    return new Promise((resolve, reject) => {
      const existing = [...document.scripts].find((script) => script.dataset.source === source);
      if (existing?.dataset.loaded === "true") return resolve();
      const script = existing || document.createElement("script");
      script.dataset.source = source;
      script.src = source;
      script.onload = () => { script.dataset.loaded = "true"; resolve(); };
      script.onerror = () => reject(new Error(`无法加载 ${source}`));
      if (!existing) document.head.appendChild(script);
    });
  }

  function scheduleLive2dFit() {
    if (state.live2d.fitFrame !== null) {
      window.cancelAnimationFrame(state.live2d.fitFrame);
    }
    state.live2d.fitFrame = window.requestAnimationFrame(() => {
      state.live2d.fitFrame = window.requestAnimationFrame(() => {
        state.live2d.fitFrame = null;
        state.live2d.fit?.();
      });
    });
  }

  function captureRenderedModelBounds(app, model) {
    const gl = app.renderer.gl;
    const pixelWidth = app.renderer.width;
    const pixelHeight = app.renderer.height;
    if (!gl || !pixelWidth || !pixelHeight || !model.scale.x || !model.scale.y) return null;

    app.renderer.render(app.stage);
    const pixels = new Uint8Array(pixelWidth * pixelHeight * 4);
    gl.readPixels(0, 0, pixelWidth, pixelHeight, gl.RGBA, gl.UNSIGNED_BYTE, pixels);

    let minX = pixelWidth;
    let minY = pixelHeight;
    let maxX = -1;
    let maxY = -1;
    for (let y = 0; y < pixelHeight; y += 1) {
      const row = y * pixelWidth * 4;
      for (let x = 0; x < pixelWidth; x += 1) {
        if (pixels[row + x * 4 + 3] <= 96) continue;
        minX = Math.min(minX, x);
        minY = Math.min(minY, y);
        maxX = Math.max(maxX, x);
        maxY = Math.max(maxY, y);
      }
    }
    if (maxX < minX || maxY < minY) return null;

    const resolution = app.renderer.resolution || 1;
    const screenLeft = minX / resolution;
    const screenRight = (maxX + 1) / resolution;
    const screenTop = (pixelHeight - maxY - 1) / resolution;
    const screenBottom = (pixelHeight - minY) / resolution;
    const localLeft = model.pivot.x + (screenLeft - model.position.x) / model.scale.x;
    const localRight = model.pivot.x + (screenRight - model.position.x) / model.scale.x;
    const localTop = model.pivot.y + (screenTop - model.position.y) / model.scale.y;
    const localBottom = model.pivot.y + (screenBottom - model.position.y) / model.scale.y;
    return {
      x: localLeft,
      y: localTop,
      width: localRight - localLeft,
      height: localBottom - localTop,
    };
  }

  async function ensureLive2dEngine() {
    if (state.live2d.app) return state.live2d.app;
    if (state.live2d.loading) return state.live2d.loading;
    if (!state.config.live2d_runtime_available || !state.config.live2d_runtime_url) {
      throw new Error("尚未安装 Live2D Cubism Core");
    }
    state.live2d.loading = (async () => {
      await loadScript("/ui/vendor/pixi.min.js");
      await loadScript(state.config.live2d_runtime_url);
      // The combined display bundle registers both adapters. Cubism 2 models
      // still require the user-supplied legacy live2d.min.js runtime; do not
      // load the deprecated vendor/cubism2.min.js adapter bundle here.
      const cubism2Url = state.config.live2d_runtime_url.replace(/live2dcubismcore\.min\.js$/, "live2d.min.js");
      try { await loadScript(cubism2Url); } catch (_) { /* optional */ }
      await loadScript("/ui/vendor/pixi-live2d-display.min.js");
      if (!window.PIXI?.live2d?.Live2DModel) throw new Error("Live2D 引擎没有正确初始化");
      const app = new window.PIXI.Application({
        view: elements.liveCanvas,
        transparent: true,
        backgroundAlpha: 0,
        antialias: true,
        autoStart: true,
        clearBeforeRender: true,
        resizeTo: elements.liveHost,
        autoDensity: true,
        resolution: Math.max(1, window.devicePixelRatio || 1),
      });
      state.live2d.app = app;
      if (window.ResizeObserver && !state.live2d.resizeObserver) {
        state.live2d.resizeObserver = new ResizeObserver(scheduleLive2dFit);
        state.live2d.resizeObserver.observe(elements.liveHost);
      }
      return app;
    })();
    try {
      return await state.live2d.loading;
    } finally {
      state.live2d.loading = null;
    }
  }

  function supersededLive2dLoad() {
    const error = new Error("Live2D 模型加载已由较新的切换请求替代");
    error.code = "LIVE2D_LOAD_SUPERSEDED";
    return error;
  }

  function isSupersededLive2dLoad(error) {
    return error?.code === "LIVE2D_LOAD_SUPERSEDED";
  }

  function destroyCurrentLive2dModel(app) {
    if (state.live2d.calibrationFrame !== null) {
      window.cancelAnimationFrame(state.live2d.calibrationFrame);
      state.live2d.calibrationFrame = null;
    }
    const previous = state.live2d.model;
    state.live2d.model = null;
    state.live2d.modelId = null;
    state.live2d.fit = null;
    state.live2d.contentBounds = null;
    if (!previous) return;
    try { app.stage.removeChild(previous); } catch (_) { /* already detached */ }
    try { previous.destroy({ children: true }); } catch (_) { /* best effort */ }
  }

  async function performLive2dModelLoad(modelInfo, loadVersion) {
    if (loadVersion !== state.live2d.loadVersion) throw supersededLive2dLoad();
    const app = await ensureLive2dEngine();
    if (loadVersion !== state.live2d.loadVersion) throw supersededLive2dLoad();
    destroyCurrentLive2dModel(app);
    const model = await window.PIXI.live2d.Live2DModel.from(modelInfo.url, { autoInteract: true });
    if (loadVersion !== state.live2d.loadVersion) {
      try { model.destroy({ children: true }); } catch (_) { /* best effort */ }
      throw supersededLive2dLoad();
    }
    app.stage.addChild(model);
    model.anchor.set(0, 0);
    model.interactive = true;
    model.on("hit", () => {
      if (handleCharacterClick()) {
        try { model.motion("TapBody"); } catch (_) { /* model-specific */ }
      }
    });
    state.live2d.model = model;
    state.live2d.modelId = modelInfo.id;
    state.live2d.contentBounds = null;

    const fit = () => {
      const width = elements.liveHost.clientWidth;
      const height = elements.liveHost.clientHeight;
      if (!width || !height) return;
      app.renderer.resize(width, height);
      model.scale.set(1);
      model.pivot.set(0, 0);
      const bounds = state.live2d.contentBounds || model.getLocalBounds();
      const naturalWidth = bounds.width || model.internalModel?.originalWidth || model.width;
      const naturalHeight = bounds.height || model.internalModel?.originalHeight || model.height;
      if (!naturalWidth || !naturalHeight) return;

      const horizontalPadding = Math.max(24, width * 0.06);
      const topPadding = Math.max(24, height * 0.045);
      const bottomPadding = elements.composer.hidden
        ? Math.max(44, height * 0.065)
        : elements.composer.offsetHeight + 36;
      const availableWidth = Math.max(120, width - horizontalPadding * 2);
      const availableHeight = Math.max(120, height - topPadding - bottomPadding);
      const fitScale = Math.min(availableWidth / naturalWidth, availableHeight / naturalHeight);
      const requestedScale = Math.max(0.2, Number(modelInfo.scale || 1));
      const calibrationSafety = state.live2d.contentBounds ? 0.72 : 0.68;
      model.scale.set(Math.min(fitScale * requestedScale, fitScale) * calibrationSafety);
      model.pivot.set(bounds.x + bounds.width / 2, bounds.y + bounds.height);
      model.position.set(width / 2, topPadding + availableHeight);
      app.renderer.render(app.stage);
    };
    state.live2d.fit = fit;
    fit();
    if (state.live2d.calibrationFrame !== null) {
      window.cancelAnimationFrame(state.live2d.calibrationFrame);
    }
    state.live2d.calibrationFrame = window.requestAnimationFrame(() => {
      state.live2d.calibrationFrame = window.requestAnimationFrame(() => {
        state.live2d.calibrationFrame = null;
        const contentBounds = captureRenderedModelBounds(app, model);
        if (contentBounds?.width > 0 && contentBounds?.height > 0) {
          state.live2d.contentBounds = contentBounds;
          fit();
        }
      });
    });
    return model;
  }

  function loadLive2dModel(modelInfo) {
    if (!modelInfo?.url) return Promise.reject(new Error("尚未导入 Live2D 模型"));
    const loadVersion = ++state.live2d.loadVersion;
    const request = state.live2d.loadSerial
      .catch(() => undefined)
      .then(() => performLive2dModelLoad(modelInfo, loadVersion));
    state.live2d.loadSerial = request.catch(() => undefined);
    return request;
  }

  function animateLive2d(emotion) {
    const model = state.live2d.model;
    if (!model || state.config.mode !== "live2d") return;
    try {
      if (emotion === "thinking") {
        model.focus(0, -0.35);
      } else if (["happy", "surprised"].includes(emotion)) {
        model.expression();
        model.motion("TapBody");
      } else if (emotion === "sad") {
        model.focus(0, 0.45);
      } else {
        model.focus(0, 0);
      }
    } catch (_) {
      // Expressions and motion group names differ between user models.
    }
  }

  function setEyeOpen(value) {
    const core = state.live2d.model?.internalModel?.coreModel;
    if (!core) return;
    for (const id of ["ParamEyeLOpen", "ParamEyeROpen", "PARAM_EYE_L_OPEN", "PARAM_EYE_R_OPEN"]) {
      try {
        if (typeof core.setParameterValueById === "function") core.setParameterValueById(id, value);
        else if (typeof core.setParamFloat === "function") core.setParamFloat(id, value);
      } catch (_) { /* model-specific eye parameters */ }
    }
  }

  function startThinkingBlink() {
    if (state.thinkingBlinkFrame !== null) return;
    const started = performance.now();
    const frame = (now) => {
      const phase = (now - started) % 820;
      const openness = phase < 95 ? Math.max(0.08, Math.abs(phase - 48) / 48) : 1;
      setEyeOpen(openness);
      state.thinkingBlinkFrame = requestAnimationFrame(frame);
    };
    state.thinkingBlinkFrame = requestAnimationFrame(frame);
  }

  function stopThinkingBlink() {
    if (state.thinkingBlinkFrame !== null) cancelAnimationFrame(state.thinkingBlinkFrame);
    state.thinkingBlinkFrame = null;
    setEyeOpen(1);
  }

  function celebrateMessageArrival() {
    elements.shell.classList.remove("message-arrived");
    void elements.shell.offsetWidth;
    elements.shell.classList.add("message-arrived");
    window.setTimeout(() => elements.shell.classList.remove("message-arrived"), 760);
    try { state.live2d.model?.motion?.("TapBody"); } catch (_) { /* optional */ }
  }

  function triggerIdleMotion() {
    if (!state.config.idle_animations || state.busy || state.speech.listening || !elements.composer.hidden) return scheduleIdleMotion();
    elements.shell.classList.add("idle-yawn");
    try { state.live2d.model?.motion?.("Idle"); } catch (_) { /* optional */ }
    window.setTimeout(() => elements.shell.classList.remove("idle-yawn"), 1900);
    scheduleIdleMotion();
  }

  function scheduleIdleMotion() {
    window.clearTimeout(state.idleTimer);
    if (!state.config.idle_animations) return;
    state.idleTimer = window.setTimeout(triggerIdleMotion, 45000 + Math.random() * 45000);
  }

  function markActivity() { scheduleIdleMotion(); }

  async function applyMode(mode, persist = false) {
    if (persist) {
      const response = await state.api.set_mode(mode);
      if (!response.ok) {
        showBubble(response.error || "无法切换角色模式");
        return false;
      }
    }
    if (mode === "live2d") {
      try {
        showBubble("正在唤醒 Live2D…", { thinking: true });
        elements.liveHost.hidden = false;
        await loadLive2dModel(state.config.selected_model);
        elements.gif.style.display = "none";
        elements.shell.classList.add("live2d-active");
        state.live2d.fit?.();
        hideBubble();
        await state.api.report_client_status("live2d_ready", state.config.selected_model?.id || "");
      } catch (error) {
        if (isSupersededLive2dLoad(error)) return false;
        elements.gif.style.display = "block";
        elements.liveHost.hidden = true;
        elements.shell.classList.remove("live2d-active");
        state.config.mode = "gif";
        showBubble(`Live2D 加载失败，已切回 GIF：${error.message}`);
        await state.api.report_client_status("live2d_error", error.message || String(error));
        setStatus("error");
        if (persist) await state.api.set_mode("gif");
        updateMenuState();
        return false;
      }
    } else {
      elements.gif.style.display = "block";
      elements.liveHost.hidden = true;
      elements.shell.classList.remove("live2d-active");
    }
    state.config.mode = mode;
    elements.shell.dataset.mode = mode;
    updateMenuState();
    return true;
  }

  function rebuildProviderSelect() {
    elements.providerSelect.replaceChildren();
    for (const provider of state.config.providers || []) {
      const option = document.createElement("option");
      option.value = provider.id;
      option.textContent = provider.available ? provider.name : `${provider.name}（不可用）`;
      option.disabled = !provider.available;
      option.selected = provider.id === state.config.provider;
      elements.providerSelect.appendChild(option);
    }
  }

  function rebuildModelSelect() {
    elements.modelSelect.replaceChildren();
    if (!(state.config.models || []).length) {
      const option = document.createElement("option");
      option.textContent = "尚未导入模型";
      option.value = "";
      elements.modelSelect.appendChild(option);
      elements.modelSelect.disabled = true;
      return;
    }
    elements.modelSelect.disabled = false;
    for (const model of state.config.models) {
      const option = document.createElement("option");
      option.value = model.id;
      option.textContent = model.name;
      option.selected = model.id === state.config.model_id;
      elements.modelSelect.appendChild(option);
    }
  }

  function openMenu(x, y) {
    elements.menu.hidden = false;
    const width = 216;
    const estimatedHeight = Math.min(300, window.innerHeight - 16);
    elements.menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - width - 8))}px`;
    elements.menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - estimatedHeight - 8))}px`;
  }

  function closeMenu() {
    elements.menu.hidden = true;
  }

  function updateMenuState() {
    state.config.live2d_available = Boolean(
      state.config.live2d_runtime_available && state.config.selected_model
    );
    elements.liveMenu.disabled = !state.config.live2d_available;
    elements.coreMenu.textContent = state.config.live2d_runtime_available
      ? "✓ Cubism Core 已安装"
      : "安装 Cubism Core";
    elements.menu.querySelectorAll('[data-action="mode"]').forEach((button) => {
      button.classList.toggle("active", button.dataset.value === state.config.mode);
    });
    elements.menu.querySelectorAll('[data-action="scale"]').forEach((button) => {
      button.classList.toggle("active", Number(button.dataset.value) === Number(state.config.scale));
    });
    elements.onTopMenu.textContent = state.config.on_top ? "取消置顶" : "保持置顶";
    rebuildProviderSelect();
    rebuildModelSelect();
  }

  async function importModel() {
    showBubble("请选择你有权使用的 Live2D 模型文件或 ZIP。", { thinking: true });
    const result = await state.api.import_live2d_model();
    if (result.cancelled) {
      showBubble("已取消导入。");
      return;
    }
    if (!result.ok) {
      showBubble(result.error || "模型导入失败");
      setEmotion("sad");
      return;
    }
    state.config.models = result.models;
    state.config.model_id = result.model.id;
    state.config.selected_model = result.model;
    state.config.live2d_available = Boolean(result.live2d_available);
    updateMenuState();
    showBubble(`已导入“${result.model.name}”。模型权利由导入者负责确认。`);
    if (state.config.live2d_available) await applyMode("live2d", true);
  }

  async function installCore() {
    const result = await state.api.install_live2d_core();
    if (result.cancelled) return;
    if (!result.ok) {
      showBubble(result.error || "Cubism Core 安装失败");
      return;
    }
    state.config.live2d_runtime_available = true;
    state.config.live2d_runtime_url = result.live2d_runtime_url;
    state.config.live2d_available = Boolean(result.live2d_available);
    updateMenuState();
    showBubble("Cubism Core 已安装到你的本地用户目录，不会上传或随程序分发。");
  }

  async function handleMenuClick(event) {
    const button = event.target.closest("button[data-action]");
    if (!button || button.disabled) return;
    const { action, value } = button.dataset;
    closeMenu();
    if (action === "settings") {
      await state.api.open_settings();
    } else if (action === "voice") {
      openComposer();
    } else if (action === "mode") {
      await applyMode(value, true);
    } else if (action === "import-model") {
      await importModel();
    } else if (action === "install-core") {
      await installCore();
    } else if (action === "model-guide") {
      await state.api.open_model_guide();
      showBubble("已打开 Live2D 官方样例页面。请先阅读对应许可，再自行下载并导入。");
    } else if (action === "scale") {
      const result = await state.api.set_scale(Number(value));
      if (result.ok) state.config.scale = result.scale;
    } else if (action === "on-top") {
      const result = await state.api.set_on_top(!state.config.on_top);
      if (result.ok) state.config.on_top = result.on_top;
    } else if (action === "minimize") {
      await state.api.minimize();
    } else if (action === "exit") {
      await state.api.exit_app();
    }
    updateMenuState();
  }

  async function handleProviderChange() {
    const previous = state.config.provider;
    const result = await state.api.set_provider(elements.providerSelect.value);
    if (result.ok) {
      state.config.provider = result.provider;
      showBubble(`已切换到 ${elements.providerSelect.selectedOptions[0]?.textContent || result.provider}。`);
    } else {
      elements.providerSelect.value = previous;
      showBubble(result.error || "智能体切换失败");
    }
  }

  async function handleModelChange() {
    const modelId = elements.modelSelect.value;
    if (!modelId) return;
    const result = await state.api.set_model(modelId, false);
    if (!result.ok) {
      showBubble(result.error || "模型切换失败");
      return;
    }
    state.config.model_id = result.model_id;
    state.config.selected_model = result.model;
    state.config.live2d_available = Boolean(result.live2d_available);
    if (state.config.mode === "live2d") {
      showBubble(`正在切换到“${result.model.name}”…`, { thinking: true });
      try {
        const switched = await applyMode("live2d", false);
        if (switched) showBubble(`已切换到“${result.model.name}”。`);
      } catch (error) {
        if (isSupersededLive2dLoad(error)) return;
        showBubble(`模型加载失败：${error.message}`);
      }
    }
    updateMenuState();
  }

  function wireEvents() {
    elements.hitbox.addEventListener("click", handleCharacterClick);
    elements.liveCanvas.addEventListener("click", handleCharacterClick);
    elements.stage.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      markActivity();
      state.drag.startX = event.screenX;
      state.drag.startY = event.screenY;
      state.drag.moved = false;
      state.drag.tracking = true;
      state.drag.pointerId = event.pointerId;
      state.drag.ready = Promise.resolve(state.api.begin_drag(event.screenX, event.screenY));
      try { elements.stage.setPointerCapture(event.pointerId); } catch (_) { /* optional */ }
    });
    document.addEventListener("pointermove", (event) => {
      if (!state.drag.tracking) return;
      const distance = Math.hypot(event.screenX - state.drag.startX, event.screenY - state.drag.startY);
      if (distance >= 5) {
        state.drag.moved = true;
        state.drag.suppressClickUntil = performance.now() + 300;
      }
      if (state.drag.moved) queueDragPosition(event.screenX, event.screenY);
    });
    document.addEventListener("pointerup", () => {
      const wasTracking = state.drag.tracking;
      const wasMoved = state.drag.moved;
      if (wasTracking && wasMoved) {
        state.drag.suppressClickUntil = performance.now() + 250;
      }
      state.drag.tracking = false;
      if (state.drag.pointerId !== null) {
        try { elements.stage.releasePointerCapture(state.drag.pointerId); } catch (_) { /* optional */ }
      }
      state.drag.pointerId = null;
      if (wasTracking && !wasMoved) handleCharacterClick();
    });
    elements.bubbleClose.addEventListener("click", hideBubble);
    elements.composer.addEventListener("submit", submitMessage);
    elements.mic.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      try { elements.mic.setPointerCapture(event.pointerId); } catch (_) { /* optional */ }
      startListening();
    });
    elements.mic.addEventListener("pointerup", (event) => {
      event.preventDefault();
      try { elements.mic.releasePointerCapture(event.pointerId); } catch (_) { /* optional */ }
      stopListening(true);
    });
    elements.mic.addEventListener("pointercancel", () => stopListening(false));
    elements.input.addEventListener("input", () => { autoSizeInput(); markActivity(); });
    elements.input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        elements.composer.requestSubmit();
      }
    });
    document.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      openMenu(event.clientX, event.clientY);
    });
    document.addEventListener("pointerdown", (event) => {
      if (!elements.menu.hidden && !elements.menu.contains(event.target)) closeMenu();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        if (state.speech.listening) stopListening(false);
        closeMenu();
        closeComposer();
      }
    });
    elements.menu.addEventListener("click", handleMenuClick);
    elements.providerSelect.addEventListener("change", handleProviderChange);
    elements.modelSelect.addEventListener("change", handleModelChange);
    window.addEventListener("resize", scheduleLive2dFit);
    window.addEventListener("blur", dismissPetUi);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) dismissPetUi();
    });
  }

  async function refreshSettings() {
    const previousMode = state.config.mode;
    const previousModel = state.config.model_id;
    const externalBubble = state.config.external_bubble;
    const next = await state.api.bootstrap();
    state.config = { ...state.config, ...next, external_bubble: externalBubble || next.external_bubble };
    updateMenuState();
    if (state.config.mode !== previousMode || state.config.model_id !== previousModel) {
      await applyMode(state.config.mode, false);
    }
  }

  async function init() {
    wireEvents();
    state.api = await connectApi();
    try {
      state.config = { ...state.config, ...(await state.api.bootstrap()) };
      updateMenuState();
      await applyMode(state.config.mode, false);
      scheduleIdleMotion();
      setStatus("ready");
      await state.api.report_client_status("ui_ready", state.config.mode);
    } catch (error) {
      showBubble(`初始化失败：${error.message}`);
      setStatus("error");
    }
  }

  window.enableExternalBubble = () => {
    state.config.external_bubble = true;
    state.typingToken += 1;
    elements.bubble.hidden = true;
  };
  window.dismissPetUi = dismissPetUi;
  window.openPetComposer = openComposer;
  window.onPetHostResize = scheduleLive2dFit;
  window.onPushToTalk = (active) => {
    if (active) startListening();
    else stopListening(true);
  };
  window.onPetSettingsChanged = () => {
    refreshSettings().catch((error) => showBubble(`设置刷新失败：${error.message || error}`));
  };
  window.getPetDiagnostics = () => {
    const rectOf = (element) => {
      const rect = element.getBoundingClientRect();
      return {
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height,
      };
    };
    const model = state.live2d.model;
    const bounds = model?.getLocalBounds?.();
    return {
      viewport: {
        width: window.innerWidth,
        height: window.innerHeight,
        devicePixelRatio: window.devicePixelRatio,
      },
      capabilities: {
        speechRecognition: Boolean(window.SpeechRecognition || window.webkitSpeechRecognition),
        mediaDevices: Boolean(navigator.mediaDevices?.getUserMedia),
        secureContext: window.isSecureContext,
      },
      shell: rectOf(elements.shell),
      stage: rectOf(elements.stage),
      composer: {
        hidden: elements.composer.hidden,
        rect: rectOf(elements.composer),
      },
      canvas: {
        width: elements.liveCanvas.width,
        height: elements.liveCanvas.height,
        rect: rectOf(elements.liveCanvas),
      },
      renderer: state.live2d.app ? {
        width: state.live2d.app.renderer.width,
        height: state.live2d.app.renderer.height,
        screenWidth: state.live2d.app.renderer.screen.width,
        screenHeight: state.live2d.app.renderer.screen.height,
      } : null,
      model: model ? {
        id: state.live2d.modelId,
        format: state.config.selected_model?.format || null,
        x: model.x,
        y: model.y,
        scaleX: model.scale.x,
        scaleY: model.scale.y,
        pivotX: model.pivot.x,
        pivotY: model.pivot.y,
        bounds: bounds ? {
          x: bounds.x,
          y: bounds.y,
          width: bounds.width,
          height: bounds.height,
        } : null,
      } : null,
    };
  };

  init();
})();

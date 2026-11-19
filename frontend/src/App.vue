<script setup>
import {
  computed,
  nextTick,
  onMounted,
  onUnmounted,
  reactive,
  ref,
  watch,
} from "vue";
import { marked } from "marked";
import DOMPurify from "dompurify";

const USER_ID = "test-user";

const threads = ref([]);
const activeThread = ref(null);
const threadMenuId = ref(null);
const userMenuOpen = ref(false);
const messages = ref([]);
const messagesEl = ref(null);
const stickToBottom = ref(true);
const inputEl = ref(null);
const input = ref("");
const currentPlan = ref(null);
const traces = ref({});
const planCollapsed = ref(false);
const planDrawerOpen = ref(false);
const streaming = ref(false);
const status = ref("");
const error = ref("");
const interrupt = ref(null);
const interruptAnswers = ref({});
const profileOpen = ref(false);
const profileSaving = ref(false);
const renameOpen = ref(false);
const renameSaving = ref(false);
const renameThreadId = ref(null);
const renameTitle = ref("");
const deleteOpen = ref(false);
const deleteSaving = ref(false);
const deleteTarget = ref(null);
const profileForm = ref({
  city: "",
  language: "zh",
  transport: "",
  food: "",
  interests: "",
  custom: "{}",
});

let controller = null;
let currentAssistant = null;

const empty = computed(
  () => messages.value.length === 0 && !streaming.value,
);

const showJump = computed(
  () => !stickToBottom.value && messages.value.length > 0,
);

const canSubmitInterrupt = computed(() =>
  Object.values(interruptAnswers.value).some(
    (value) => value && value.trim(),
  ),
);

const activeTitle = computed(() => {
  const item = threads.value.find(
    (thread) => thread.thread_id === activeThread.value,
  );
  return item?.title || "新的旅程";
});

const hasPlanTasks = computed(
  () => Boolean((currentPlan.value?.tasks || []).length),
);

const planStats = computed(() => {
  const stats = {
    total: 0,
    pending: 0,
    running: 0,
    success: 0,
    failed: 0,
  };

  for (const task of currentPlan.value?.tasks || []) {
    const state = taskState(task);

    stats.total += 1;
    stats[state] += 1;
  }

  return stats;
});

function taskState(task) {
  const trace = traces.value[String(task.task_id)];

  if (trace?.phase === "start") {
    return "running";
  }

  if (trace?.phase === "success") {
    return "success";
  }

  if (trace?.phase === "failed") {
    return "failed";
  }

  return {
    completed: "success",
    failed: "failed",
    in_progress: "running",
  }[task.status] || "pending";
}

function taskStateLabel(state) {
  return {
    pending: "待执行",
    running: "执行中",
    success: "完成",
    failed: "失败",
  }[state] || "待执行";
}

const suggestions = [
  "帮我安排长沙一日游，主要去岳麓山、橘子洲、五一广场",
  "五一广场附近有什么不辣的餐厅？",
  "从岳麓山到橘子洲怎么走？",
  "最近几天长沙天气怎么样？",
];

function scrollToBottom(force = false) {
  nextTick(() => {
    const element = messagesEl.value;

    if (!element || (!force && !stickToBottom.value)) {
      return;
    }

    element.scrollTop = element.scrollHeight;
  });
}

function onMessagesScroll() {
  const element = messagesEl.value;

  if (!element) {
    return;
  }

  const distance = element.scrollHeight - element.scrollTop - element.clientHeight;
  stickToBottom.value = distance < 80;
}

function jumpToBottom() {
  stickToBottom.value = true;
  scrollToBottom(true);
}

function planKey(threadId) {
  return `travel-plan:${threadId}`;
}

function traceKey(threadId) {
  return `travel-trace:${threadId}`;
}

function cancelKey(threadId) {
  return `travel-cancel:${threadId}`;
}

function saveCancelNotice(threadId, content) {
  if (!threadId || !content) {
    return;
  }

  try {
    localStorage.setItem(cancelKey(threadId), content);
  } catch {
    // ignore
  }
}

function loadCancelNotice(threadId) {
  if (!threadId) {
    return "";
  }

  try {
    return localStorage.getItem(cancelKey(threadId)) || "";
  } catch {
    return "";
  }
}

function clearCancelNotice(threadId) {
  if (!threadId) {
    return;
  }

  try {
    localStorage.removeItem(cancelKey(threadId));
  } catch {
    // ignore
  }
}

function saveTrace(threadId, value) {
  if (!threadId) {
    return;
  }

  try {
    localStorage.setItem(traceKey(threadId), JSON.stringify(value));
  } catch {
    // ignore storage errors
  }
}

function loadTrace(threadId) {
  if (!threadId) {
    return {};
  }

  try {
    const raw = localStorage.getItem(traceKey(threadId));
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function clearTrace(threadId) {
  traces.value = {};

  if (!threadId) {
    return;
  }

  try {
    localStorage.removeItem(traceKey(threadId));
  } catch {
    // ignore
  }
}

function formatDuration(duration) {
  if (duration == null) {
    return "";
  }

  if (duration < 1000) {
    return `${duration}ms`;
  }

  return `${(duration / 1000).toFixed(1)}s`;
}

function savePlan(threadId, plan) {
  if (!threadId || !plan) {
    return;
  }

  try {
    localStorage.setItem(planKey(threadId), JSON.stringify(plan));
  } catch {
    // ignore storage errors
  }
}

function loadPlan(threadId) {
  if (!threadId) {
    return null;
  }

  try {
    const raw = localStorage.getItem(planKey(threadId));
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function togglePlanPanel() {
  planCollapsed.value = !planCollapsed.value;
}

function clearPlan() {
  currentPlan.value = null;

  if (activeThread.value) {
    try {
      localStorage.removeItem(planKey(activeThread.value));
    } catch {
      // ignore
    }
  }
}

function togglePlanDrawer() {
  planDrawerOpen.value = !planDrawerOpen.value;
}

watch(status, () => scrollToBottom());
watch(() => messages.value.length, () => scrollToBottom());

function resizeInput() {
  nextTick(() => {
    const element = inputEl.value;

    if (!element) {
      return;
    }

    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 190)}px`;
  });
}

watch(input, resizeInput);

function renderMarkdown(content) {
  // 温度区间里的 ~ 会被 Markdown 当成删除线语法，先转成全角波浪号
  const text = String(content || "").replace(/~/g, "～");

  try {
    return DOMPurify.sanitize(
      marked.parse(text),
    );
  } catch {
    return DOMPurify.sanitize(text);
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-User-Id": USER_ID,
      ...(options.headers || {}),
    },
  });

  if (!response.ok) {
    let message = `请求失败 (${response.status})`;

    try {
      const data = await response.json();
      message = data.detail || message;
    } catch {
      // ignore
    }

    throw new Error(message);
  }

  return response.json();
}

async function refreshThreads() {
  try {
    const data = await api("/api/threads");
    threads.value = data.items || [];
  } catch (exc) {
    error.value = exc.message;
  }
}

function toggleThreadMenu(threadId) {
  threadMenuId.value = threadMenuId.value === threadId ? null : threadId;
}

function closeThreadMenu() {
  threadMenuId.value = null;
  userMenuOpen.value = false;
}

function toggleUserMenu() {
  userMenuOpen.value = !userMenuOpen.value;
  threadMenuId.value = null;
}

async function openThread(threadId) {
  threadMenuId.value = null;

  if (streaming.value) {
    error.value = "正在生成回答，请先停止生成或等待完成后再切换会话。";
    return;
  }

  activeThread.value = threadId;
  status.value = "";

  try {
    const data = await api(
      `/api/threads/${encodeURIComponent(threadId)}/messages`,
    );

    messages.value = (data.items || []).map((item) => ({
      role: item.role,
      content: item.content,
      name: item.name,
    }));

    const cancelNotice = loadCancelNotice(threadId);

    if (
      cancelNotice
      && !messages.value.some((item) => item.content === cancelNotice)
    ) {
      messages.value.push({
        role: "assistant",
        content: cancelNotice,
        name: "assistant",
      });
    }

    currentPlan.value = loadPlan(threadId);
    traces.value = loadTrace(threadId);
    planCollapsed.value = false;
    planDrawerOpen.value = Boolean(currentPlan.value);
    scrollToBottom(true);
  } catch (exc) {
    error.value = exc.message;
  }
}

function newChat() {
  if (streaming.value) {
    error.value = "正在生成回答，请先停止生成或等待完成。";
    return;
  }

  activeThread.value = null;
  messages.value = [];
  currentPlan.value = null;
  traces.value = {};
  planCollapsed.value = false;
  planDrawerOpen.value = false;
  input.value = "";
  status.value = "";
  error.value = "";
}

function renameThread(thread) {
  threadMenuId.value = null;
  renameThreadId.value = thread.thread_id;
  renameTitle.value = thread.title || "新会话";
  renameOpen.value = true;
}

async function saveRename() {
  const title = renameTitle.value.trim();

  if (!title || !renameThreadId.value) {
    return;
  }

  renameSaving.value = true;

  try {
    await api(
      `/api/threads/${encodeURIComponent(renameThreadId.value)}`,
      {
        method: "PATCH",
        body: JSON.stringify({ title }),
      },
    );

    renameOpen.value = false;
    await refreshThreads();
  } catch (exc) {
    error.value = exc.message;
  } finally {
    renameSaving.value = false;
  }
}

function deleteThread(thread) {
  threadMenuId.value = null;
  deleteTarget.value = thread;
  deleteOpen.value = true;
}

async function confirmDelete() {
  if (!deleteTarget.value) {
    return;
  }

  const target = deleteTarget.value;
  deleteSaving.value = true;

  try {
    await api(`/api/threads/${encodeURIComponent(target.thread_id)}`, {
      method: "DELETE",
    });

    if (activeThread.value === target.thread_id) {
      newChat();
    }

    try {
      localStorage.removeItem(planKey(target.thread_id));
      localStorage.removeItem(traceKey(target.thread_id));
      localStorage.removeItem(cancelKey(target.thread_id));
    } catch {
      // ignore
    }

    deleteOpen.value = false;
    deleteTarget.value = null;
    await refreshThreads();
  } catch (exc) {
    error.value = exc.message;
  } finally {
    deleteSaving.value = false;
  }
}

function parseSseBlock(block) {
  const dataLines = block
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim());

  if (!dataLines.length) {
    return null;
  }

  try {
    return JSON.parse(dataLines.join("\n"));
  } catch {
    return null;
  }
}

async function streamRequest(url, body, assistant) {
  controller = new AbortController();

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-User-Id": USER_ID,
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    if (!response.ok || !response.body) {
      throw new Error(`请求失败 (${response.status})`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();

      if (done) {
        break;
      }

      buffer += decoder.decode(value, { stream: true });

      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() || "";

      for (const block of blocks) {
        const event = parseSseBlock(block);

        if (event) {
          handleEvent(event, assistant);
        }
      }
    }
  } catch (exc) {
    if (exc.name === "AbortError") {
      return;
    }

    error.value = exc.message;
  } finally {
    streaming.value = false;
    controller = null;
    await refreshThreads();
  }
}

function handleEvent(event, assistant) {
  switch (event.event) {
    case "meta":
      activeThread.value = event.thread_id;
      break;

    case "token":
      assistant.content += event.content || "";
      scrollToBottom();
      break;

    case "plan":
      status.value = "正在拆解行程…";

      if (event.planning) {
        const tasks = event.planning.tasks || [];

        if (tasks.length) {
          currentPlan.value = event.planning;
          planCollapsed.value = false;
          planDrawerOpen.value = false;
          savePlan(activeThread.value, event.planning);
        } else {
          currentPlan.value = null;

          try {
            localStorage.removeItem(planKey(activeThread.value));
          } catch {
            // ignore
          }
        }
      }
      break;

    case "summary":
      status.value = event.message || "正在压缩历史…";
      break;

    case "planning":
      status.value = event.message || "正在规划…";
      break;

    case "router":
      status.value = event.message || "正在判断…";
      break;

    case "trace": {
      const taskId = String(event.task_id ?? "");

      if (taskId) {
        traces.value = {
          ...traces.value,
          [taskId]: {
            ...(traces.value[taskId] || {}),
            ...event,
          },
        };
        saveTrace(activeThread.value, traces.value);
      }
      break;
    }

    case "subagent":
    case "recall":
    case "answer":
    case "respond":
      status.value = event.message || "正在处理…";
      break;

    case "interrupt":
      interrupt.value = event.payload || {};
      interruptAnswers.value = {};

      for (const item of interrupt.value.questions || []) {
        if (item.field) {
          interruptAnswers.value[item.field] = "";
        }
      }

      streaming.value = false;
      status.value = "";
      break;

    case "done":
      assistant.content = event.answer || assistant.content;
      status.value = "";
      currentAssistant = null;

      if (event.cancelled) {
        currentPlan.value = null;
        clearTrace(activeThread.value);

        try {
          localStorage.removeItem(planKey(activeThread.value));
        } catch {
          // ignore
        }
      }

      scheduleProfileRefresh();
      scrollToBottom();
      break;

    case "error":
      error.value = event.message || "执行失败";
      streaming.value = false;
      break;

    default:
      break;
  }
}

async function sendMessage() {
  const text = input.value.trim();

  if (!text || streaming.value) {
    return;
  }

  input.value = "";
  error.value = "";
  status.value = "正在准备…";
  clearPlan();
  clearTrace(activeThread.value);
  clearCancelNotice(activeThread.value);

  messages.value.push({
    role: "user",
    content: text,
  });

  const assistant = reactive({
    role: "assistant",
    content: "",
    name: "assistant",
  });

  messages.value.push(assistant);
  currentAssistant = assistant;
  streaming.value = true;
  scrollToBottom(true);

  await streamRequest(
    "/api/chat/stream",
    {
      thread_id: activeThread.value,
      message: text,
    },
    assistant,
  );
}

async function submitInterrupt() {
  if (!interrupt.value || !activeThread.value) {
    return;
  }

  const resume = {};

  for (const [key, value] of Object.entries(interruptAnswers.value)) {
    if (value && value.trim()) {
      resume[key] = value.trim();
    }
  }

  interrupt.value = null;
  streaming.value = true;
  status.value = "正在继续规划…";

  if (!currentAssistant) {
    currentAssistant = reactive({
      role: "assistant",
      content: "",
      name: "assistant",
    });
    messages.value.push(currentAssistant);
  }

  await streamRequest(
    "/api/chat/resume",
    {
      thread_id: activeThread.value,
      resume,
    },
    currentAssistant,
  );
}

async function cancelInterrupt() {
  if (!interrupt.value || !activeThread.value) {
    return;
  }

  interrupt.value = null;
  currentPlan.value = null;

  try {
    localStorage.removeItem(planKey(activeThread.value));
  } catch {
    // ignore
  }
  streaming.value = true;
  status.value = "正在取消…";

  if (!currentAssistant) {
    currentAssistant = reactive({
      role: "assistant",
      content: "",
      name: "assistant",
    });
    messages.value.push(currentAssistant);
  }

  await streamRequest(
    "/api/chat/resume",
    {
      thread_id: activeThread.value,
      resume: { action: "cancel" },
    },
    currentAssistant,
  );
}

function stopStreaming() {
  if (controller) {
    controller.abort();
  }

  if (currentAssistant) {
    const notice = "已停止生成，你可以修改需求后重新发送。";

    currentAssistant.content = currentAssistant.content
      ? `${currentAssistant.content}\n\n${notice}`
      : notice;
    saveCancelNotice(activeThread.value, currentAssistant.content);
    currentAssistant = null;
  }

  if (currentPlan.value) {
    currentPlan.value = {
      ...currentPlan.value,
      tasks: (currentPlan.value.tasks || []).map((task) => {
        if (task.status !== "in_progress") {
          return task;
        }

        const trace = traces.value[task.task_id] || {};

        traces.value = {
          ...traces.value,
          [task.task_id]: {
            ...trace,
            task_id: task.task_id,
            agent: task.agent,
            phase: "failed",
            error: "用户已停止生成",
          },
        };

        return {
          ...task,
          status: "failed",
        };
      }),
    };

    savePlan(activeThread.value, currentPlan.value);
    saveTrace(activeThread.value, traces.value);
  }

  streaming.value = false;
  status.value = "";
  scrollToBottom(true);
}

async function loadProfile({ quiet = false } = {}) {
  try {
    const data = await api("/api/profile");
    const profile = data.profile || {};

    profileForm.value = {
      city: profile.city || "",
      language: profile.language || "zh",
      transport: (profile.transport || []).join("、"),
      food: (profile.food || []).join("、"),
      interests: (profile.interests || []).join("、"),
      custom: JSON.stringify(profile.custom || {}, null, 2),
    };
  } catch (exc) {
    if (!quiet) {
      error.value = exc.message;
    }
  }
}

async function openProfile() {
  userMenuOpen.value = false;
  profileOpen.value = true;
  await loadProfile();
}

function scheduleProfileRefresh() {
  for (const delay of [1500, 5000, 10000]) {
    window.setTimeout(
      () => loadProfile({ quiet: true }),
      delay,
    );
  }
}

async function saveProfile() {
  profileSaving.value = true;

  try {
    let custom = {};

    try {
      custom = JSON.parse(profileForm.value.custom || "{}");
    } catch {
      throw new Error("custom 不是合法 JSON");
    }

    const profile = {
      city: profileForm.value.city.trim(),
      language: profileForm.value.language.trim() || "zh",
      transport: profileForm.value.transport
        .split(/[、,，]/)
        .map((item) => item.trim())
        .filter(Boolean),
      food: profileForm.value.food
        .split(/[、,，]/)
        .map((item) => item.trim())
        .filter(Boolean),
      interests: profileForm.value.interests
        .split(/[、,，]/)
        .map((item) => item.trim())
        .filter(Boolean),
      custom,
    };

    await api("/api/profile", {
      method: "PUT",
      body: JSON.stringify({ profile }),
    });

    profileOpen.value = false;
  } catch (exc) {
    error.value = exc.message;
  } finally {
    profileSaving.value = false;
  }
}

function logout() {
  userMenuOpen.value = false;

  if (streaming.value) {
    stopStreaming();
  }

  newChat();
  profileOpen.value = false;
}

function pickSuggestion(text) {
  input.value = text;
}

onMounted(() => {
  refreshThreads();
  loadProfile({ quiet: true });
  document.addEventListener("click", closeThreadMenu);

  window.addEventListener("focus", () => {
    loadProfile({ quiet: true });
  });
});

onUnmounted(() => {
  document.removeEventListener("click", closeThreadMenu);
});
</script>

<template>
  <div
    class="app-shell"
    :class="{
      'with-plan': hasPlanTasks,
      'plan-collapsed': hasPlanTasks && planCollapsed,
    }"
  >
    <aside class="sidebar">
      <div class="brand">
        <div>
          <strong>JSep</strong>
          <span>智能出行助手</span>
        </div>
      </div>

      <button class="new-chat" @click="newChat">
        <span>+</span> 新的旅程
      </button>

      <div class="thread-scroll">
        <div class="thread-label">最近会话</div>

        <div
          v-for="thread in threads"
          :key="thread.thread_id"
          class="thread-item"
          :class="{
            active: thread.thread_id === activeThread,
            'menu-open': threadMenuId === thread.thread_id,
          }"
          role="button"
          tabindex="0"
          @click="openThread(thread.thread_id)"
          @keydown.enter="openThread(thread.thread_id)"
        >
          <div class="thread-main">
            <span class="thread-title">
              {{ thread.title || "新会话" }}
            </span>
            <span class="thread-preview">
              {{ thread.preview || "还没有消息" }}
            </span>
          </div>

          <div class="thread-actions">
            <button
              class="thread-menu-btn"
              title="会话操作"
              aria-label="会话操作"
              :aria-expanded="threadMenuId === thread.thread_id"
              @click.stop="toggleThreadMenu(thread.thread_id)"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <circle cx="5" cy="12" r="1.6" />
                <circle cx="12" cy="12" r="1.6" />
                <circle cx="19" cy="12" r="1.6" />
              </svg>
            </button>

            <div
              v-if="threadMenuId === thread.thread_id"
              class="thread-menu"
              @click.stop
            >
              <button
                class="thread-menu-item"
                @click.stop="renameThread(thread)"
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path
                    d="M4 20h4.2L19.4 8.8a2.1 2.1 0 0 0 0-3L18.2 4.6a2.1 2.1 0 0 0-3 0L4 15.8V20Z"
                  />
                  <path d="m13.9 6.1 4 4" />
                </svg>
                <span>重命名</span>
              </button>

              <button
                class="thread-menu-item danger"
                @click.stop="deleteThread(thread)"
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M4 7h16" />
                  <path d="M9 7V4h6v3" />
                  <path d="M7 7l1 13h8l1-13" />
                  <path d="M10 11v5M14 11v5" />
                </svg>
                <span>删除</span>
              </button>
            </div>
          </div>
        </div>

        <div v-if="!threads.length" class="thread-empty">
          还没有会话，先出发吧。
        </div>
      </div>

      <div class="user-area">
        <button
          class="profile-entry"
          :aria-expanded="userMenuOpen"
          @click.stop="toggleUserMenu"
        >
          <div class="avatar">我</div>
          <div class="user-copy">
            <strong>{{ USER_ID }}</strong>
            <span>本地测试账号</span>
          </div>
          <span class="chevron">›</span>
        </button>

        <div
          v-if="userMenuOpen"
          class="user-menu"
          @click.stop
        >
          <button class="user-menu-item" @click="openProfile">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M4 7h16M4 12h16M4 17h16" />
              <circle cx="9" cy="7" r="2" />
              <circle cx="15" cy="12" r="2" />
              <circle cx="8" cy="17" r="2" />
            </svg>
            <span>设置</span>
          </button>

          <button
            class="user-menu-item danger"
            @click="logout"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M10 5H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h4" />
              <path d="M15 8l4 4-4 4M19 12H9" />
            </svg>
            <span>退出登录</span>
          </button>
        </div>
      </div>
    </aside>

    <main class="chat-panel">
      <header class="chat-header">
        <div>
          <h1>{{ activeTitle }}</h1>
          <p class="status muted">
            说走就走之前，先让我帮你把路捋顺。
          </p>
        </div>

        <div class="header-actions">
          <button
            v-if="hasPlanTasks"
            class="plan-pill"
            @click="togglePlanDrawer"
          >
            行程计划
          </button>
        </div>
      </header>

      <section
        ref="messagesEl"
        class="messages"
        @scroll.passive="onMessagesScroll"
      >
        <div v-if="empty" class="welcome">
          <div class="welcome-mark">📍</div>
          <h2>下一站，去哪里？</h2>
          <p>
            我可以查地点、排路线、看天气，也能按你的口味找吃的。
          </p>

          <div class="suggestions">
            <button
              v-for="item in suggestions"
              :key="item"
              @click="pickSuggestion(item)"
            >
              {{ item }}
            </button>
          </div>
        </div>

        <div
          v-for="(message, index) in messages"
          :key="index"
          class="message-row"
          :class="message.role"
        >
          <div class="bubble-wrap">
            <div
              v-if="message.name && message.name !== 'assistant'"
              class="agent-name"
            >
              {{ message.name }}
            </div>

            <div
              v-if="message.role === 'assistant'"
              class="bubble markdown"
            >
              <div
                v-if="!message.content && streaming"
                class="typing inline"
              >
                <span></span><span></span><span></span>
              </div>

              <div
                v-else
                v-html="renderMarkdown(message.content)"
              ></div>
            </div>

            <div v-else class="bubble user-bubble">
              {{ message.content }}
            </div>
          </div>
        </div>

        <div v-if="status" class="agent-progress">
          <span class="pulse"></span>
          <span>{{ status }}</span>
        </div>

      </section>

      <button
        v-if="showJump && !interrupt"
        class="jump-bottom"
        aria-label="回到最新"
        data-tooltip="回到最新"
        @click="jumpToBottom"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M12 5v14" />
          <path d="m5.5 12.5 6.5 6.5 6.5-6.5" />
        </svg>
      </button>

      <div v-if="error" class="error-bar">
        {{ error }}
        <button @click="error = ''">×</button>
      </div>

      <footer class="composer">
        <div v-if="interrupt" class="interrupt-inline">
          <div class="interrupt-inline-head">
            <span class="eyebrow">需要你补充一点信息</span>
            <strong>再确认一下，行程会更准</strong>
          </div>

          <div class="question-list">
            <label
              v-for="(item, index) in interrupt.questions || []"
              :key="item.field || index"
              class="question"
            >
              <span>{{ item.question || item.field }}</span>
              <input
                v-model="interruptAnswers[item.field]"
                placeholder="请输入补充信息"
              />
              <small
                v-if="item.reason"
                class="question-reason"
              >
                {{ item.reason }}
              </small>
            </label>
          </div>

          <div class="interrupt-inline-actions">
            <button class="ghost-btn" @click="cancelInterrupt">
              取消任务
            </button>
            <button
              class="primary-btn"
              :disabled="!canSubmitInterrupt"
              @click="submitInterrupt"
            >
              提交并继续
            </button>
          </div>
        </div>

        <template v-else>
          <div class="composer-box">
            <textarea
              ref="inputEl"
              v-model="input"
              rows="1"
              placeholder="想去哪儿？想吃什么？告诉我你的计划…"
              @input="resizeInput"
              @keydown.enter.exact.prevent="sendMessage"
            ></textarea>

            <button
              v-if="streaming"
              class="stop-btn"
              aria-label="停止生成"
              title="停止生成"
              @click="stopStreaming"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <rect x="7" y="7" width="10" height="10" rx="2" />
              </svg>
            </button>

            <button
              v-else
              class="send-btn"
              aria-label="发送"
              title="发送"
              :disabled="!input.trim()"
              @click="sendMessage"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 19V5" />
                <path d="m5.5 11.5 6.5-6.5 6.5 6.5" />
              </svg>
            </button>
          </div>

          <p class="composer-hint">
            Enter 发送 · Shift + Enter 换行 · AI 可能会犯错，请核对关键信息
          </p>
        </template>
      </footer>
    </main>

    <div
      v-if="hasPlanTasks && planDrawerOpen"
      class="plan-drawer-mask"
      @click="planDrawerOpen = false"
    ></div>

    <aside
      v-if="hasPlanTasks"
      class="plan-sidebar"
      :class="{
        collapsed: planCollapsed,
        'drawer-open': planDrawerOpen,
      }"
    >
      <button
        class="plan-sidebar-toggle"
        title="规划详情"
        @click="togglePlanPanel"
      >
        <svg
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.8"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <path d="m15 6-6 6 6 6" />
        </svg>
        <span>{{ planCollapsed ? "规划详情" : "收起规划" }}</span>
      </button>

      <div v-if="!planCollapsed" class="plan-sidebar-body">
        <div class="plan-sidebar-head">
          <h2>{{ currentPlan.goal }}</h2>
        </div>

        <div
          v-if="(currentPlan.tasks || []).length"
          class="plan-summary"
        >
          <span>
            <b>{{ planStats.success }}</b>/{{ planStats.total }} 完成
          </span>
          <span v-if="planStats.running" class="running">
            {{ planStats.running }} 执行中
          </span>
          <span v-if="planStats.failed" class="failed">
            {{ planStats.failed }} 失败
          </span>
        </div>

        <div class="plan-tasks">
          <div
            v-for="task in currentPlan.tasks || []"
            :key="task.task_id"
            class="plan-task"
            :class="`is-${taskState(task)}`"
          >
            <span
              class="plan-index"
              :class="taskState(task)"
            ></span>

            <div class="plan-task-body">
              <p class="plan-task-title">{{ task.content }}</p>

              <div class="plan-task-meta">
                <span class="plan-agent">
                  {{ task.agent }}
                </span>

                <span class="plan-meta-right">
                  <em
                    v-if="traces[task.task_id]?.duration_ms != null"
                    class="plan-duration"
                  >
                    {{ formatDuration(traces[task.task_id].duration_ms) }}
                  </em>

                  <span
                    class="plan-status"
                    :class="taskState(task)"
                  >
                    {{ taskStateLabel(taskState(task)) }}
                  </span>
                </span>
              </div>

              <details
                v-if="
                  taskState(task) === 'failed'
                    && traces[task.task_id]?.error
                "
                class="plan-error"
              >
                <summary>失败原因</summary>
                <p>{{ traces[task.task_id].error }}</p>
              </details>
            </div>
          </div>
        </div>

        <div
          v-if="(currentPlan.missing || []).length"
          class="plan-missing"
        >
          还需要补充：
          {{ currentPlan.missing.map((item) => item.field).join("、") }}
        </div>
      </div>
    </aside>

    <div v-if="deleteOpen" class="modal-mask" @click.self="deleteOpen = false">
      <section class="modal delete-modal">
        <div class="modal-head">
          <div>
            <span class="eyebrow">删除会话</span>
            <h2>确定删除这段旅程？</h2>
          </div>
          <button class="close-btn" @click="deleteOpen = false">×</button>
        </div>

        <div class="delete-copy">
          <p>
            将删除
            <strong>「{{ deleteTarget?.title || "新会话" }}」</strong>
            的对话记录、checkpoint 和相关记忆。
          </p>
          <p>删除后无法恢复。</p>
        </div>

        <div class="modal-actions">
          <button class="ghost-btn" @click="deleteOpen = false">取消</button>
          <button
            class="danger-btn"
            :disabled="deleteSaving"
            @click="confirmDelete"
          >
            {{ deleteSaving ? "删除中…" : "删除" }}
          </button>
        </div>
      </section>
    </div>

    <div v-if="renameOpen" class="modal-mask" @click.self="renameOpen = false">
      <section class="modal rename-modal">
        <div class="modal-head">
          <div>
            <span class="eyebrow">会话名称</span>
            <h2>重命名这段旅程</h2>
          </div>
          <button class="close-btn" @click="renameOpen = false">×</button>
        </div>

        <label class="rename-field">
          <span>名称</span>
          <input
            v-model="renameTitle"
            maxlength="50"
            autofocus
            placeholder="例如：长沙周末两日游"
            @keyup.enter="saveRename"
          />
        </label>

        <div class="modal-actions">
          <button class="ghost-btn" @click="renameOpen = false">取消</button>
          <button
            class="primary-btn"
            :disabled="renameSaving || !renameTitle.trim()"
            @click="saveRename"
          >
            {{ renameSaving ? "保存中…" : "确定" }}
          </button>
        </div>
      </section>
    </div>

    <div v-if="profileOpen" class="modal-mask" @click.self="profileOpen = false">
      <section class="modal profile-modal settings-modal">
        <div class="settings-layout">
          <aside class="settings-nav">
            <strong>设置</strong>
            <button class="settings-nav-item active">偏好设置</button>
          </aside>

          <div class="settings-content">
            <div class="modal-head">
              <div>
                <h2>偏好设置</h2>
                <p>保存后，规划、餐厅和路线都会参考这些偏好。</p>
              </div>
              <button class="close-btn" @click="profileOpen = false">×</button>
            </div>

        <div class="form-grid">
          <label>
            <span>常驻城市</span>
            <input v-model="profileForm.city" placeholder="例如：长沙" />
          </label>

          <label>
            <span>偏好语言</span>
            <input v-model="profileForm.language" placeholder="zh / 中文" />
          </label>

          <label>
            <span>出行方式</span>
            <input v-model="profileForm.transport" placeholder="地铁、步行、打车" />
          </label>

          <label>
            <span>口味 / 饮食</span>
            <input v-model="profileForm.food" placeholder="不吃辣、清淡、人均 80" />
          </label>

          <label>
            <span>游玩兴趣</span>
            <input v-model="profileForm.interests" placeholder="博物馆、爬山、夜景" />
          </label>

          <label class="wide">
            <span>其他偏好（JSON）</span>
            <textarea v-model="profileForm.custom" rows="4"></textarea>
          </label>
        </div>

            <div class="modal-actions">
              <button class="ghost-btn" @click="profileOpen = false">取消</button>
              <button class="primary-btn" :disabled="profileSaving" @click="saveProfile">
                {{ profileSaving ? "保存中…" : "保存偏好" }}
              </button>
            </div>
          </div>
        </div>
      </section>
    </div>

  </div>
</template>

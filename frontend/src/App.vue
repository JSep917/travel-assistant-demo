<script setup>
import {
  computed,
  nextTick,
  onMounted,
  reactive,
  ref,
  watch,
} from "vue";
import { marked } from "marked";
import DOMPurify from "dompurify";

const USER_ID = "test-user";

const threads = ref([]);
const activeThread = ref(null);
const messages = ref([]);
const messagesEl = ref(null);
const input = ref("");
const currentPlan = ref(null);
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

const suggestions = [
  "帮我安排长沙一日游，主要去岳麓山、橘子洲、五一广场",
  "五一广场附近有什么不辣的餐厅？",
  "从岳麓山到橘子洲怎么走？",
  "最近几天长沙天气怎么样？",
];

function scrollToBottom() {
  nextTick(() => {
    const element = messagesEl.value;

    if (element) {
      element.scrollTop = element.scrollHeight;
    }
  });
}

function planKey(threadId) {
  return `travel-plan:${threadId}`;
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

watch(status, scrollToBottom);
watch(() => messages.value.length, scrollToBottom);

function renderMarkdown(content) {
  try {
    return DOMPurify.sanitize(
      marked.parse(content || ""),
    );
  } catch {
    return DOMPurify.sanitize(content || "");
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

async function openThread(threadId) {
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

    currentPlan.value = loadPlan(threadId);
    planCollapsed.value = false;
    planDrawerOpen.value = Boolean(currentPlan.value);
    scrollToBottom();
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
  planCollapsed.value = false;
  planDrawerOpen.value = false;
  input.value = "";
  status.value = "";
  error.value = "";
}

function renameThread(thread) {
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
        currentPlan.value = event.planning;
        planCollapsed.value = false;
        planDrawerOpen.value = false;
        savePlan(activeThread.value, event.planning);
      }
      break;

    case "planning":
      status.value = event.message || "正在规划…";
      break;

    case "router":
      status.value = event.message || "正在判断…";
      break;

    case "subagent":
    case "recall":
    case "answer":
    case "respond":
      status.value = event.message || "正在处理…";
      break;

    case "task_timing":
      status.value = "正在整理结果…";
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
  scrollToBottom();

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

  streaming.value = false;
  status.value = "";
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

function pickSuggestion(text) {
  input.value = text;
}

onMounted(() => {
  refreshThreads();
  loadProfile({ quiet: true });

  window.addEventListener("focus", () => {
    loadProfile({ quiet: true });
  });
});
</script>

<template>
  <div
    class="app-shell"
    :class="{
      'with-plan': currentPlan,
      'plan-collapsed': currentPlan && planCollapsed,
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
          :class="{ active: thread.thread_id === activeThread }"
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
              class="icon-btn"
              title="重命名"
              aria-label="重命名会话"
              @click.stop="renameThread(thread)"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path
                  d="M4 20h4.2L19.4 8.8a2.1 2.1 0 0 0 0-3L18.2 4.6a2.1 2.1 0 0 0-3 0L4 15.8V20Z"
                />
                <path d="m13.9 6.1 4 4" />
              </svg>
            </button>

            <button
              class="icon-btn danger"
              title="删除"
              aria-label="删除会话"
              @click.stop="deleteThread(thread)"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M4 7h16" />
                <path d="M9 7V4h6v3" />
                <path d="M7 7l1 13h8l1-13" />
                <path d="M10 11v5M14 11v5" />
              </svg>
            </button>
          </div>
        </div>

        <div v-if="!threads.length" class="thread-empty">
          还没有会话，先出发吧。
        </div>
      </div>

      <button class="profile-entry" @click="openProfile">
        <div class="avatar">我</div>
        <div>
          <strong>我的偏好</strong>
          <span>让推荐更懂你</span>
        </div>
        <span class="chevron">›</span>
      </button>
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
            v-if="currentPlan"
            class="plan-pill"
            @click="togglePlanDrawer"
          >
            行程计划
          </button>
          <span class="user-chip">{{ USER_ID }}</span>
        </div>
      </header>

      <section ref="messagesEl" class="messages">
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
              v-html="renderMarkdown(message.content)"
            ></div>

            <div v-else class="bubble user-bubble">
              {{ message.content }}
            </div>
          </div>
        </div>

        <div v-if="status" class="agent-progress">
          <span class="pulse"></span>
          <span>{{ status }}</span>
        </div>

        <div v-if="streaming" class="typing">
          <span></span><span></span><span></span>
        </div>
      </section>

      <div v-if="error" class="error-bar">
        {{ error }}
        <button @click="error = ''">×</button>
      </div>

      <footer class="composer">
        <div class="composer-box">
          <textarea
            v-model="input"
            rows="1"
            placeholder="想去哪儿？想吃什么？告诉我你的计划…"
            @keydown.enter.exact.prevent="sendMessage"
          ></textarea>

          <button
            v-if="streaming"
            class="stop-btn"
            @click="stopStreaming"
          >
            停止
          </button>

          <button
            v-else
            class="send-btn"
            :disabled="!input.trim()"
            @click="sendMessage"
          >
            发送
          </button>
        </div>

        <p class="composer-hint">
          Enter 发送 · Shift + Enter 换行 · AI 可能会犯错，请核对关键信息
        </p>
      </footer>
    </main>

    <div
      v-if="currentPlan && planDrawerOpen"
      class="plan-drawer-mask"
      @click="planDrawerOpen = false"
    ></div>

    <aside
      v-if="currentPlan"
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
          <span class="eyebrow">
            {{ (currentPlan.tasks || []).length ? "本次行程规划" : "规划前确认" }}
          </span>
          <h2>{{ currentPlan.goal }}</h2>
        </div>

        <div class="plan-tasks">
          <div
            v-for="task in currentPlan.tasks || []"
            :key="task.task_id"
            class="plan-task"
          >
            <span class="plan-index">{{ task.task_id }}</span>
            <div class="plan-task-body">
              <p>{{ task.content }}</p>
              <span class="plan-agent">{{ task.agent }}</span>
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
      <section class="modal profile-modal">
        <div class="modal-head">
          <div>
            <h2>我的旅行偏好</h2>
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
      </section>
    </div>

    <div v-if="interrupt" class="modal-mask">
      <section class="modal interrupt-modal">
        <div class="modal-head">
          <div>
            <span class="eyebrow">需要你补充一点信息</span>
            <h2>再确认一下，行程会更准</h2>
          </div>
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
              :placeholder="item.reason || '请补充'"
            />
          </label>

          <p v-if="!(interrupt.questions || []).length" class="muted">
            {{ interrupt.message || "继续执行需要补充信息。" }}
          </p>
        </div>

        <div class="modal-actions">
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
      </section>
    </div>
  </div>
</template>

/**
 * 对话问答视图：基于新闻证据的会话式问答。
 *
 * 回答文本按【新闻证据】与【背景知识】两个标题切分为分区，以不同底色
 * 渲染；文中 [n] 引用可点击展开引用卡片；evidence_mode 为 insufficient
 * 时在消息底部显示"证据不足"标识条。
 */

import { useRef, useState, type FormEvent, type ReactNode } from "react";

import { isUnavailable, sendChat, type ChatAnswer, type Citation } from "../api";

// ---------------------------------------------------------------------------
// 回答文本切分
// ---------------------------------------------------------------------------

type SegmentLabel = "新闻证据" | "背景知识" | null;

interface AnswerSegment {
  label: SegmentLabel;
  text: string;
}

const SEGMENT_MARKERS: Array<Exclude<SegmentLabel, null>> = ["新闻证据", "背景知识"];

/** 按两个固定标题字符串把回答切成分区；无标题时整体为普通分区。 */
function splitAnswer(text: string): AnswerSegment[] {
  const hits: Array<{ index: number; label: Exclude<SegmentLabel, null> }> = [];
  for (const label of SEGMENT_MARKERS) {
    const marker = `【${label}】`;
    let position = text.indexOf(marker);
    while (position !== -1) {
      hits.push({ index: position, label });
      position = text.indexOf(marker, position + marker.length);
    }
  }
  hits.sort((a, b) => a.index - b.index);
  if (hits.length === 0) {
    return [{ label: null, text }];
  }
  const segments: AnswerSegment[] = [];
  if (hits[0].index > 0) {
    segments.push({ label: null, text: text.slice(0, hits[0].index) });
  }
  hits.forEach((hit, order) => {
    const start = hit.index + hit.label.length + 2; // 跳过标题及左右括号
    const end = order + 1 < hits.length ? hits[order + 1].index : text.length;
    segments.push({ label: hit.label, text: text.slice(start, end).replace(/^\s+/, "") });
  });
  return segments;
}

// ---------------------------------------------------------------------------
// 消息模型与渲染
// ---------------------------------------------------------------------------

interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  answer?: ChatAnswer;
  isError?: boolean;
}

/** 将分区文本中的 [n] 渲染为可点击引用；无对应引用时保留原文。 */
function renderWithCitations(
  text: string,
  citations: Citation[],
  onToggle: (index: number) => void,
): ReactNode[] {
  const parts = text.split(/(\[\d+\])/g);
  return parts.map((part, order) => {
    const match = /^\[(\d+)\]$/.exec(part);
    if (match === null) {
      return part;
    }
    const citationIndex = Number(match[1]);
    const citation = citations.find((item) => item.index === citationIndex);
    if (citation === undefined) {
      return part;
    }
    return (
      <button
        key={`cite-${order}`}
        type="button"
        className="chat-cite-btn"
        onClick={() => onToggle(citationIndex)}
      >
        [{citationIndex}]
      </button>
    );
  });
}

function AssistantMessage({ message }: { message: ChatMessage }) {
  const [expandedCitation, setExpandedCitation] = useState<number | null>(null);
  const answer = message.answer;

  if (answer === undefined) {
    return (
      <div className="chat-bubble">
        <span className={message.isError ? "error-text" : undefined}>{message.text}</span>
      </div>
    );
  }

  const toggle = (index: number) => {
    setExpandedCitation((previous) => (previous === index ? null : index));
  };
  const expanded =
    expandedCitation === null
      ? null
      : (answer.citations.find((item) => item.index === expandedCitation) ?? null);
  const segments = splitAnswer(answer.text);

  return (
    <div className="chat-bubble">
      {segments.map((segment, order) =>
        segment.label === null ? (
          <div className="chat-segment plain" key={order}>
            {renderWithCitations(segment.text, answer.citations, toggle)}
          </div>
        ) : (
          <div
            className={`chat-segment ${segment.label === "新闻证据" ? "evidence" : "background"}`}
            key={order}
          >
            <span className="chat-segment-label">【{segment.label}】</span>
            {renderWithCitations(segment.text, answer.citations, toggle)}
          </div>
        ),
      )}
      {expanded !== null ? (
        <div className="chat-cite-card">
          <div>
            [{expanded.index}] {expanded.title}
          </div>
          <div className="news-meta">
            <span>{expanded.source}</span>
            <span>可见时间 {expanded.visible_ts}</span>
          </div>
        </div>
      ) : null}
      {answer.evidence_mode === "insufficient" ? (
        <div className="chat-insufficient">证据不足：检索到的新闻不足以支撑确定结论</div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 视图主体
// ---------------------------------------------------------------------------

export function ChatView() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [symbol, setSymbol] = useState("");
  const [asOf, setAsOf] = useState("");
  const [pending, setPending] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    // 等待新消息渲染后再滚动到底部。
    window.requestAnimationFrame(() => {
      const node = scrollRef.current;
      if (node !== null) {
        node.scrollTop = node.scrollHeight;
      }
    });
  };

  const handleSubmit = async (formEvent: FormEvent) => {
    formEvent.preventDefault();
    const trimmed = question.trim();
    if (trimmed === "" || pending) {
      return;
    }
    setMessages((previous) => [...previous, { role: "user", text: trimmed }]);
    setQuestion("");
    setPending(true);
    scrollToBottom();
    try {
      const answer = await sendChat({
        question: trimmed,
        symbol: symbol.trim() || undefined,
        as_of: asOf ? (asOf.length === 16 ? `${asOf}:00` : asOf) : undefined,
      });
      setMessages((previous) => [...previous, { role: "assistant", text: answer.text, answer }]);
    } catch (error) {
      const text = isUnavailable(error)
        ? "问答引擎未装配：当前服务实例未装配问答组件，请装配后重试。"
        : `回答失败：${error instanceof Error ? error.message : String(error)}`;
      setMessages((previous) => [...previous, { role: "assistant", text, isError: !isUnavailable(error) }]);
    } finally {
      setPending(false);
      scrollToBottom();
    }
  };

  return (
    <div className="chat-layout">
      <div className="chat-context">
        <div className="field">
          <label className="field-label" htmlFor="chat-symbol">
            标的代码（可选）
          </label>
          <input
            id="chat-symbol"
            className="input"
            value={symbol}
            onChange={(changeEvent) => setSymbol(changeEvent.target.value)}
            placeholder="例如 600519.SH"
            style={{ width: 160 }}
          />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="chat-asof">
            as_of 时点（可选）
          </label>
          <input
            id="chat-asof"
            className="input"
            type="datetime-local"
            value={asOf}
            onChange={(changeEvent) => setAsOf(changeEvent.target.value)}
          />
          <span className="field-hint">回答仅使用该时点前可见的新闻</span>
        </div>
      </div>

      <div className="chat-scroll" ref={scrollRef}>
        {messages.length === 0 ? (
          <p className="muted" style={{ textAlign: "center", marginTop: "var(--space-4)" }}>
            就存量新闻提问，回答附带可核查的引用。
          </p>
        ) : (
          messages.map((message, order) => (
            <div className={`chat-msg ${message.role}`} key={order}>
              {message.role === "user" ? (
                <div className="chat-bubble">{message.text}</div>
              ) : (
                <AssistantMessage message={message} />
              )}
            </div>
          ))
        )}
        {pending ? (
          <div className="chat-msg assistant">
            <div className="chat-bubble muted">正在检索证据并生成回答……</div>
          </div>
        ) : null}
      </div>

      <form className="chat-inputbar" onSubmit={(e) => void handleSubmit(e)}>
        <input
          className="input"
          value={question}
          onChange={(changeEvent) => setQuestion(changeEvent.target.value)}
          placeholder="输入问题，例如：该公司近期是否有回购计划"
        />
        <button type="submit" className="btn btn-primary" disabled={pending || question.trim() === ""}>
          发送
        </button>
      </form>
    </div>
  );
}

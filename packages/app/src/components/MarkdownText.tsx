/**
 * Markdown 渲染组件(0.5.1)— assistant 消息文本走它。
 *
 * - GFM 子集(表格 / 任务列表 / 删除线)走 `remark-gfm`
 * - 代码块走 `react-syntax-highlighter` Prism light + 按需注册语言
 *   (按需 = 体积小;未注册的语言 fallback 成无高亮的等宽块,不会崩)
 * - inline `code` 用 muted 背景 + monospace
 * - 安全:react-markdown 默认不渲染原始 HTML,XSS 不用额外管
 *
 * 设计原则:封装 markdown 实现细节,Chat.tsx caller 只 `<MarkdownText text=... />`。
 */

import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import SyntaxHighlighter from "react-syntax-highlighter/dist/esm/prism-light";
import oneDark from "react-syntax-highlighter/dist/esm/styles/prism/one-dark";
import remarkGfm from "remark-gfm";

import bash from "react-syntax-highlighter/dist/esm/languages/prism/bash";
import css from "react-syntax-highlighter/dist/esm/languages/prism/css";
import go from "react-syntax-highlighter/dist/esm/languages/prism/go";
import json from "react-syntax-highlighter/dist/esm/languages/prism/json";
import jsx from "react-syntax-highlighter/dist/esm/languages/prism/jsx";
import markdown from "react-syntax-highlighter/dist/esm/languages/prism/markdown";
import python from "react-syntax-highlighter/dist/esm/languages/prism/python";
import rust from "react-syntax-highlighter/dist/esm/languages/prism/rust";
import sql from "react-syntax-highlighter/dist/esm/languages/prism/sql";
import toml from "react-syntax-highlighter/dist/esm/languages/prism/toml";
import tsx from "react-syntax-highlighter/dist/esm/languages/prism/tsx";
import typescript from "react-syntax-highlighter/dist/esm/languages/prism/typescript";
import yaml from "react-syntax-highlighter/dist/esm/languages/prism/yaml";

// 按需注册,bundle 小;新增语言加在这里即可
SyntaxHighlighter.registerLanguage("bash", bash);
SyntaxHighlighter.registerLanguage("sh", bash);
SyntaxHighlighter.registerLanguage("shell", bash);
SyntaxHighlighter.registerLanguage("css", css);
SyntaxHighlighter.registerLanguage("go", go);
SyntaxHighlighter.registerLanguage("json", json);
SyntaxHighlighter.registerLanguage("javascript", jsx);
SyntaxHighlighter.registerLanguage("js", jsx);
SyntaxHighlighter.registerLanguage("jsx", jsx);
SyntaxHighlighter.registerLanguage("markdown", markdown);
SyntaxHighlighter.registerLanguage("md", markdown);
SyntaxHighlighter.registerLanguage("python", python);
SyntaxHighlighter.registerLanguage("py", python);
SyntaxHighlighter.registerLanguage("rust", rust);
SyntaxHighlighter.registerLanguage("rs", rust);
SyntaxHighlighter.registerLanguage("sql", sql);
SyntaxHighlighter.registerLanguage("toml", toml);
SyntaxHighlighter.registerLanguage("tsx", tsx);
SyntaxHighlighter.registerLanguage("typescript", typescript);
SyntaxHighlighter.registerLanguage("ts", typescript);
SyntaxHighlighter.registerLanguage("yaml", yaml);
SyntaxHighlighter.registerLanguage("yml", yaml);

export function MarkdownText({ text }: { text: string }) {
  // remarkPlugins 用 useMemo 避免每次 re-render 重建数组(react-markdown 会拿它做 deps)
  const remarkPlugins = useMemo(() => [remarkGfm], []);

  // 不依赖 @tailwindcss/typography 插件,常用 markdown 元素显式给 Tailwind class。
  // 段落 / 列表 / 标题 / 表格按 chariot UI 风格统一收紧到 text-sm,行距适中。
  return (
    <div className="break-words text-sm leading-relaxed [&>*+*]:mt-2">
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        components={{
          // 代码块 / inline code 分两路;react-markdown v10 的 code 组件用 className=`language-xxx`
          // 区分 fence(带 lang)和 inline。inline 没 className。
          code(props) {
            const { children, className, ...rest } = props;
            const match = /language-(\w+)/.exec(className ?? "");
            const isInline = !match;
            if (isInline) {
              return (
                <code
                  className="rounded bg-muted px-1 py-0.5 font-mono text-[0.875em]"
                  {...rest}
                >
                  {children}
                </code>
              );
            }
            const lang = match[1];
            const code = String(children).replace(/\n$/, "");
            return (
              <SyntaxHighlighter
                language={lang}
                style={oneDark}
                PreTag="div"
                customStyle={{
                  margin: 0,
                  borderRadius: "0.375rem",
                  fontSize: "0.8125rem",
                  lineHeight: "1.4",
                }}
              >
                {code}
              </SyntaxHighlighter>
            );
          },
          // 默认 markdown 链接走 _blank,避免在 Tauri webview 内导航走丢
          a(props) {
            return (
              <a
                {...props}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary underline underline-offset-2"
              />
            );
          },
          h1: (p) => <h1 className="mt-3 text-base font-semibold" {...p} />,
          h2: (p) => <h2 className="mt-3 text-sm font-semibold" {...p} />,
          h3: (p) => <h3 className="mt-2 text-sm font-semibold" {...p} />,
          ul: (p) => <ul className="ml-5 list-disc space-y-1" {...p} />,
          ol: (p) => <ol className="ml-5 list-decimal space-y-1" {...p} />,
          li: (p) => <li className="leading-snug" {...p} />,
          blockquote: (p) => (
            <blockquote
              className="border-l-2 border-border pl-3 italic text-muted-foreground"
              {...p}
            />
          ),
          table: (p) => (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-xs" {...p} />
            </div>
          ),
          th: (p) => (
            <th className="border border-border bg-muted/50 px-2 py-1 text-left" {...p} />
          ),
          td: (p) => <td className="border border-border px-2 py-1" {...p} />,
          hr: () => <hr className="border-border" />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

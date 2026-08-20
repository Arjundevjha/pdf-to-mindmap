import React, { useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkMath from 'remark-math';
import remarkGfm from 'remark-gfm';
import rehypeKatex from 'rehype-katex';
import katex from 'katex';
import 'katex/dist/katex.min.css';

export interface MathRendererProps {
  content: string;
  inline?: boolean;
  block?: boolean;
  className?: string;
}

/**
 * Prepares the raw string for AST parsing:
 * 1. Repairs corrupted ASCII control characters from JSON decoders.
 * 2. Reconstructs stripped LaTeX macro prefixes.
 * 3. Normalizes Unicode math symbols to LaTeX equivalents.
 * 4. Wraps unwrapped equations into $...$ so remarkMath catches them.
 */
export function prepareMathInput(raw: string): string {
  if (!raw) return '';
  let s = raw;

  // 0. Repair step markers like {1}:, {2}:, [1]:, (1):
  s = s.replace(/(?:^|\n)\s*[\{\[\(](\d+)[\}\]\)]\s*:\s*/gm, '\n- **Step $1**: ');

  // 1. Repair ASCII control characters corrupted by JSON decoders
  s = s
    .replace(/\x0c/g, '\\f') // Formfeed to \f (e.g. \frac)
    .replace(/\x08/g, '\\b') // Backspace to \b (e.g. \beta, \bar)
    .replace(/\x0b/g, '\\v') // Vertical tab to \v (e.g. \vec)
    .replace(/\r\n/g, '\n');

  // 2. Reconstruct LaTeX keywords where tab/newline/carriage-return corrupted the backslash prefix
  s = s
    .replace(/\t(ext|heta|imes|an|anh|au|o|riangle|ilde|ag|op|extbf|extit)(?=[^a-zA-Z]|$)/g, '\\t$1')
    .replace(/\r(ight|ho|angle|oot|ightarrow|e)(?=[^a-zA-Z]|$)/g, '\\r$1')
    .replace(/\n(eq|abla|u|otin|orm|ot)(?=[^a-zA-Z]|$)/g, '\\n$1')
    .replace(/\f(rac|orall)(?=[^a-zA-Z]|$)/g, '\\f$1');

  // 3. Normalize Unicode math symbols to LaTeX equivalents
  s = s
    .replace(/[\u2013\u2014\u2212]/g, '-') // en-dash, em-dash, minus sign
    .replace(/×/g, '\\times ')
    .replace(/÷/g, '\\div ')
    .replace(/±/g, '\\pm ')
    .replace(/≠/g, '\\neq ')
    .replace(/≤/g, '\\le ')
    .replace(/≥/g, '\\ge ')
    .replace(/≈/g, '\\approx ')
    .replace(/π/g, '\\pi ')
    .replace(/θ/g, '\\theta ')
    .replace(/Δ/g, '\\Delta ')
    .replace(/√/g, '\\sqrt ')
    .replace(/∞/g, '\\infty ')
    .replace(/→/g, ' $\\to$ ')
    .replace(/⇒/g, ' $\\implies$ ')
    .replace(/²/g, '^2')
    .replace(/³/g, '^3')
    .replace(/\\degree/g, '^{\\circ}')
    .replace(/°/g, '^{\\circ}');

  // 4. Heal leading LaTeX command immediately outside inline math: e.g. \Delta$(k)>0$ -> $\Delta(k)>0$
  s = s.replace(/(\\[a-zA-Z]+)\s*\$([^$]+)\$/g, '$$$1 $2$$');
  s = s.replace(/\$(\\[a-zA-Z]+)\s+([(\[{])/g, '$$$1$2');

  // 5. Heal standalone LaTeX command outside $ followed by operators or arguments:
  // e.g. \Delta(k) > 0 -> $\Delta(k) > 0$, \Delta > 0 -> $\Delta > 0$
  s = s.replace(/(?<!\$|\\)(\\Delta|\\alpha|\\beta|\\gamma|\\theta|\\pi|\\sigma|\\lambda|\\mu|\\omega)(?:\(([a-zA-Z0-9_,+-]+)\))?\s*([><=≠≤≥≈])\s*([a-zA-Z0-9_+-]+|\\[a-zA-Z]+)(?!\$)/g, '$$$1$2 $3 $4$$');

  // 6. Heal trailing argument or operator outside closing $:
  // e.g. $\Delta$(k)>0$ -> $\Delta(k)>0$, $\Delta$(k) -> $\Delta(k)$
  s = s.replace(/\$([^$]+)\$\s*(\([a-zA-Z0-9_,+-]+\))(?!\$)/g, '$$$1$2$$');
  s = s.replace(/\$([^$]+)\$\s*([><=≠≤≥≈])\s*([a-zA-Z0-9_+-]+|\\[a-zA-Z]+)(?!\$)/g, '$$$1 $2 $3$$');

  // 7. Heal adjacent or split math blocks: e.g. $\Delta$$(k)>0$ -> $\Delta(k)>0$
  s = s.replace(/\$([^$]+)\$\s*\$([^$]+)\$/g, '$$$1 $2$$');

  // 8. Fix unparenthesized linear+fraction expressions before exponents (e.g. x+\frac{b}{2a}^2 -> \left(x+\frac{b}{2a}\right)^2)
  s = s.replace(/((?:[a-zA-Z0-9]|\\[a-zA-Z]+)\s*[+-]\s*\\frac\{[^{}]*\}\{[^{}]*\})\s*\^(\d+|\{[^{}]*\})/g, '\\left($1\\right)^$2');

  // 9. Repair truncated/unclosed fraction in conjugate rationalization:
  s = s.replace(/For\s+p\s*\+\s*q\s*A\s*,?\s*multiply\s+by\s*(?:\\frac\{)?(?:\\sqrt\{p\})?\$?/gi, 'For $\\frac{A}{\\sqrt{p} + \\sqrt{q}}$, multiply numerator and denominator by $\\frac{\\sqrt{p} - \\sqrt{q}}{\\sqrt{p} - \\sqrt{q}}$');
  s = s.replace(/\\frac\{([^{}]+)\}\$/g, '\\frac{$1}{\\sqrt{p} - \\sqrt{q}}$');

  // 10. Fix bracket sizing macros (\biglx -> \bigl(x, \biglc -> \bigl(c, \bigr^2 -> \bigr)^2, \bigr$$ -> \bigr)$$)
  s = s.replace(/\\(bigl|Bigl|biggl|Biggl|left)\s*([a-zA-Z0-9])/g, (_, p1, p2) => `\\${p1}(${p2}`);
  s = s.replace(/\\(bigl|Bigl|biggl|Biggl|left)(?=[^(\[{|.\s]|$)/g, (_, p1) => `\\${p1}(`);
  s = s.replace(/\\(bigr|Bigr|biggr|Biggr|right)\s*(?=[\^+\-*=,;:]|\$\$|\$|\s|[a-zA-Z0-9]|$)/g, (_, p1) => `\\${p1})`);
  s = s.replace(/\\(bigr|Bigr|biggr|Biggr|right)(?=[^)\\]}|.\s]|$)/g, (_, p1) => `\\${p1})`);

  // 6. Split and extract English sentences embedded inside $$...$$ blocks
  s = s.replace(/\$\$([\s\S]+?)\$\$/g, (_, inner) => {
    let trailingPunct = '';
    const punctMatch = inner.match(/([.,;:!?]+)$/);
    if (punctMatch) {
      trailingPunct = punctMatch[1];
      inner = inner.slice(0, -trailingPunct.length);
    }

    const textSplitter = /(Takesquarerootsandsolveforx:?|[A-Za-z]{10,}:?|[A-Za-z]+(?:\s+[A-Za-z]+){2,}:?|(?:Completing\s+the\s+square|solving\s+for|to\s+find|and\s+solve|yielding|giving|where)\s*:?)/i;

    if (textSplitter.test(inner)) {
      const parts = inner.split(textSplitter);
      let out = '';
      for (const part of parts) {
        if (!part) continue;
        const trimmed = part.trim();
        if (!trimmed) continue;

        const isEnglish = (
          /Takesquarerootsandsolveforx/i.test(trimmed) ||
          (/^[A-Za-z\s:,;!?-]+$/.test(trimmed) && !/^\\[a-zA-Z]+$/.test(trimmed) && trimmed.length > 5) ||
          /^(Completing|solving|to|and|yielding|giving|where)/i.test(trimmed)
        );

        if (isEnglish) {
          let textPhrase = trimmed;
          if (/Takesquarerootsandsolveforx/i.test(trimmed)) {
            textPhrase = 'Take square roots and solve for $x$:';
          }
          out += `\n\n${textPhrase}\n\n`;
        } else {
          out += `$$${trimmed}$$`;
        }
      }
      return out + trailingPunct;
    }

    return `$$${inner}$$${trailingPunct}`;
  });

  // 7. Repair trailing unbalanced $$ (e.g. "ax^2 + bx + c to a\bigl(x + \frac{b}{2a}\bigr)^2 + \bigl(c - \frac{b^2}{4a}\bigr)$$ to find vertex")
  s = s.replace(/(?:^|(?<=[:\n\r\t]|\s{2,}))\s*([^$\n\r]+?)\$\$/gm, (_, expr) => {
    const clean = expr.trim().replace(/(?<=[a-zA-Z0-9)\]^_}])\s+to\s+(?=[a-zA-Z0-9(\[\\])/g, ' \\to ');
    return `$${clean}$`;
  });

  // 8. Convert parenthesized math expressions like (f(x)=a^{x}), ((e^{rt})), (a>1), (0<a<1)
  // Ensure we DO NOT match parentheses that are preceded by \bigl, \left, etc.
  s = s.replace(/(?<!\\[a-zA-Z]+)\(\(\s*([^()]+?)\s*\)\)/g, (match, inner) => {
    const isMath = /[_^\\/+\-*=<>~]|\b(e|x|y|a|b|c|t|n|k|i|pi|theta|ln|log|exp)\b/i.test(inner);
    return isMath ? `$${inner.trim()}$` : match;
  });

  s = s.replace(/(?<!\\[a-zA-Z]+)\(\s*([a-zA-Z0-9_+\-/*^\\=<> ,.{}()]+?)\s*\)/g, (match, inner) => {
    const isMath = /[=<>^_\\{}]|\b(frac|sqrt|ln|log|sin|cos|tan|exp|lim|theta|alpha|beta|pi|Delta)\b/i.test(inner)
      || /^([a-zA-Z]\([a-zA-Z0-9_+/*\-]+\)|[a-zA-Z]|\d+\/\d+)$/.test(inner.trim());
    
    const isEnglishProse = /^(e\.g\.|i\.e\.|see |note |step |fig |case |page |ref )/i.test(inner.trim());
    if (isMath && !isEnglishProse) {
      return `$${inner.trim()}$`;
    }
    return match;
  });

  // 7. Tokenize all existing $...$ and $$...$$ so we don't double-wrap commands inside math blocks
  const mathBlocks: string[] = [];
  s = s.replace(/(\$\$[\s\S]+?\$\$|\$[^$\n\r]+?\$)/g, (match) => {
    const token = `___PROTECTED_MATH_${mathBlocks.length}___`;
    mathBlocks.push(match);
    return token;
  });

  // 8. Outside protected math: convert standalone LaTeX commands not enclosed in $
  s = s.replace(
    /\\(to|rightarrow|Rightarrow|implies|iff|pm|mp|times|cdot|approx|neq|le|ge|equiv|alpha|beta|gamma|Gamma|delta|Delta|epsilon|theta|Theta|lambda|Lambda|mu|pi|Pi|sigma|Sigma|omega|Omega|phi|Phi|psi|sum|int|partial|nabla|frac\{[^{}]*\}\{[^{}]*\}|sqrt\{[^{}]*\})/g,
    (_, p1) => `$\\${p1}$`
  );

  // 9. Outside protected math: wrap un-delimited equations
  s = s.replace(
    /(?:^|(?<=[:\n\r\t]|\s{2,}))\s*((?:\\?(?:log|ln|sin|cos|tan|cot|sec|csc|exp|lim|frac|sqrt|Delta|theta|alpha|beta|gamma|pi|sum|int|bigl|bigr)|[a-zA-Z0-9()\[\]_+\-/*^]+)\s*(?:[_^]\w+|\([a-zA-Z0-9_+/*\- ,.]+\))?\s*(?:=|\\approx|\\le|\\ge|\\neq|\\pm|\\to|\\implies|<|>)\s*[a-zA-Z0-9()\[\]_+\-/*^\\.,\s]+)(?=$|[.\n\r])/gm,
    (fullMatch, expr) => {
      const isMath = /[_^\\/+\-*=]|\b(log|ln|sin|cos|tan|exp|lim|sqrt|frac|Delta|theta|pi|alpha|beta|bigl|bigr)\b/i.test(expr);
      if (!isMath) return fullMatch;

      let cleanExpr = expr.trim();
      cleanExpr = cleanExpr.replace(/(?<!\\)\b(log|ln|sin|cos|tan|sec|csc|cot|arcsin|arccos|arctan|sinh|cosh|tanh|exp|lim|max|min|det|gcd)(?=[^a-zA-Z]|$)/g, '\\$1');
      cleanExpr = cleanExpr.replace(/(?<!\\)\b(alpha|beta|gamma|delta|Delta|epsilon|zeta|eta|theta|Theta|iota|kappa|lambda|Lambda|mu|nu|xi|Xi|pi|Pi|rho|sigma|Sigma|tau|upsilon|phi|Phi|chi|psi|Psi|omega|Omega)(?=[^a-zA-Z]|$)/g, '\\$1');

      return fullMatch.replace(expr, `$${cleanExpr}$`);
    }
  );

  // 10. Restore protected math blocks
  mathBlocks.forEach((block, i) => {
    s = s.replace(`___PROTECTED_MATH_${i}___`, block);
  });

  // 11. Normalize multiple backslashes
  s = s.replace(/\\{2,}([a-zA-Z]+)/g, '\\$1');

  return s;
}

/**
 * Safely renders a single formula using KaTeX with robust error recovery.
 */
export function renderKaTeXFormula(rawFormula: string, displayMode: boolean): string {
  if (!rawFormula) return '';
  let formula = prepareMathInput(rawFormula).trim();
  // Strip outer delimiters if present
  formula = formula
    .replace(/^(\$\$|\\\[|\\\(|\$)/, '')
    .replace(/(\$\$|\\\]|\\\)|\$)$/, '')
    .trim();

  if (!formula) return '';

  try {
    return katex.renderToString(formula, {
      displayMode,
      throwOnError: false,
      output: 'html',
      strict: false,
      trust: true,
      errorColor: '#2563eb',
    });
  } catch (err) {
    console.warn('KaTeX render error for formula:', formula, err);
    return `<span class="font-mono text-xs text-blue-700 bg-blue-50 px-1 py-0.5 rounded">${formula}</span>`;
  }
}

/**
 * Production-Grade AST Math & Markdown Renderer Component
 */
export const MathRenderer: React.FC<MathRendererProps> = React.memo(({ content, inline = false, block = false, className = '' }) => {
  const prepared = useMemo(() => prepareMathInput(content), [content]);

  if (!content) return null;

  if (block) {
    const html = renderKaTeXFormula(content, true);
    return (
      <div 
        className={`katex-block-wrapper my-2.5 overflow-x-auto select-text ${className}`}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    );
  }

  if (inline) {
    return (
      <span className={`font-sans leading-normal ${className}`}>
        <ReactMarkdown
          remarkPlugins={[remarkMath, remarkGfm]}
          rehypePlugins={[[rehypeKatex, { output: 'html', throwOnError: false }]]}
          components={{
            p: ({ children }) => <span className="inline select-text">{children}</span>,
            strong: ({ children }) => <strong className="font-semibold text-slate-900">{children}</strong>,
            em: ({ children }) => <em className="italic text-slate-700">{children}</em>,
            code: ({ children }) => <code className="font-mono text-xs bg-slate-100 px-1 py-0.5 rounded">{children}</code>,
          }}
        >
          {prepared}
        </ReactMarkdown>
      </span>
    );
  }

  return (
    <div className={`font-sans text-xs leading-relaxed ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkMath, remarkGfm]}
        rehypePlugins={[[rehypeKatex, { output: 'html', throwOnError: false }]]}
        components={{
          h1: ({ children }) => <h1 className="text-sm font-bold text-slate-900 tracking-tight mt-4 mb-2">{children}</h1>,
          h2: ({ children }) => <h2 className="text-xs font-bold text-slate-800 uppercase tracking-wider mt-3.5 mb-1.5">{children}</h2>,
          h3: ({ children }) => <h3 className="text-xs font-bold text-slate-800 uppercase tracking-wider mt-3 mb-1">{children}</h3>,
          p: ({ children }) => <p className="text-xs text-slate-700 leading-relaxed my-1.5">{children}</p>,
          ul: ({ children }) => <ul className="my-1.5 pl-4 list-disc space-y-1">{children}</ul>,
          ol: ({ children }) => <ol className="my-1.5 pl-4 list-decimal space-y-1">{children}</ol>,
          li: ({ children }) => <li className="text-xs text-slate-600 leading-relaxed">{children}</li>,
          strong: ({ children }) => <strong className="font-semibold text-slate-900">{children}</strong>,
          em: ({ children }) => <em className="italic text-slate-700">{children}</em>,
          table: ({ children }) => (
            <div className="overflow-x-auto my-2">
              <table className="min-w-full text-xs text-slate-700 border-collapse border border-slate-200">
                {children}
              </table>
            </div>
          ),
          th: ({ children }) => <th className="border border-slate-200 bg-slate-50 px-2.5 py-1 text-left font-semibold text-slate-800">{children}</th>,
          td: ({ children }) => <td className="border border-slate-200 px-2.5 py-1 text-slate-600">{children}</td>,
          code: ({ children }) => <code className="font-mono text-xs bg-slate-100 text-slate-800 px-1 py-0.5 rounded">{children}</code>,
        }}
      >
        {prepared}
      </ReactMarkdown>
    </div>
  );
});

export default MathRenderer;


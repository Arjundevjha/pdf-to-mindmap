import { useMemo } from 'react';
import katex from 'katex';
import 'katex/dist/katex.min.css';

interface MathRendererProps {
  content: string;
  block?: boolean;
  className?: string;
}

/**
 * Normalizes corrupted control characters and unescaped LaTeX sequences.
 */
function cleanLatexString(raw: string): string {
  if (!raw) return '';
  return raw
    .replace(/\x0c/g, '\\f') // Formfeed to \f (e.g. \frac)
    .replace(/\x08/g, '\\b') // Backspace to \b (e.g. \beta, \bar)
    .replace(/\x0b/g, '\\v') // Vertical tab
    .replace(/\r\n/g, '\n')
    .replace(/\\degree/g, '^{\\circ}')
    .replace(/°/g, '^{\\circ}');
}

/**
 * Safely renders a single formula using KaTeX with robust error recovery.
 */
function renderKaTeXFormula(rawFormula: string, displayMode: boolean): string {
  const formula = cleanLatexString(rawFormula).trim().replace(/^\$\$|\$\$$|^\\\(|\\\)$|^\$|\$$/g, '').trim();
  if (!formula) return '';

  try {
    return katex.renderToString(formula, {
      displayMode,
      throwOnError: false,
      output: 'htmlAndMathml',
      strict: false,
      trust: true,
    });
  } catch (err) {
    console.warn('KaTeX render error for formula:', formula, err);
    return `<span class="font-mono text-xs text-blue-700 bg-blue-50 px-1 py-0.5 rounded">${formula}</span>`;
  }
}

/**
 * Renders LaTeX formulas ($...$ or $$...$$) and mixed markdown text containing math expressions
 * synchronously using KaTeX for instant, zero-reflow rendering.
 */
export function MathRenderer({ content, block = false, className = '' }: MathRendererProps) {
  const renderedContent = useMemo(() => {
    if (!content) return '';

    const cleaned = cleanLatexString(content);

    // If pure block formula requested directly
    if (block) {
      return renderKaTeXFormula(cleaned, true);
    }

    try {
      // 1. Replace $$...$$ block math first
      let processed = cleaned.replace(/\$\$([\s\S]+?)\$\$/g, (_, math) => {
        const html = renderKaTeXFormula(math, true);
        return `<div class="katex-block-wrapper my-2.5 overflow-x-auto select-text">${html}</div>`;
      });

      // 2. Replace $...$ inline math
      processed = processed.replace(/\$([^$\n]+?)\$/g, (_, math) => {
        const html = renderKaTeXFormula(math, false);
        return `<span class="katex-inline-wrapper select-text">${html}</span>`;
      });

      // 3. Replace standalone LaTeX commands (e.g. \frac{a}{b} or (n-2) \times 180^\circ) that might not be wrapped in $
      processed = processed.replace(/(\\(?:frac|sqrt|theta|pm|times|cdot|alpha|beta|gamma|Delta|sum|int|partial|approx|le|ge|neq)\b[^\s,.\n<]+)/g, (match) => {
        // Only wrap if not already inside an HTML tag or katex wrapper
        if (match.includes('class=') || match.includes('<')) return match;
        const html = renderKaTeXFormula(match, false);
        return `<span class="katex-inline-wrapper select-text">${html}</span>`;
      });

      // 4. Format markdown headers and bullets cleanly
      processed = processed.replace(/^### (.*$)/gim, '<h3 class="text-xs font-bold text-slate-800 uppercase tracking-wider mt-3 mb-1.5">$1</h3>');
      processed = processed.replace(/^## (.*$)/gim, '<h2 class="text-xs font-bold text-slate-800 uppercase tracking-wider mt-3.5 mb-1.5">$1</h2>');
      processed = processed.replace(/\*\*(.*?)\*\*/g, '<strong class="font-semibold text-slate-900">$1</strong>');
      processed = processed.replace(/^- (.*$)/gim, '<li class="text-xs text-slate-600 leading-relaxed ml-3 list-disc">$1</li>');
      processed = processed.replace(/\n\n/g, '<br class="my-1" />');

      return processed;
    } catch {
      return content;
    }
  }, [content, block]);

  return (
    <div 
      className={`font-sans text-xs leading-relaxed ${className}`}
      dangerouslySetInnerHTML={{ __html: renderedContent }}
    />
  );
}

/**
 * Pure Single-Formula KaTeX Component
 */
export function KaTeXEquation({ formula, displayMode = false, className = '' }: { formula: string; displayMode?: boolean; className?: string }) {
  const html = useMemo(() => {
    return renderKaTeXFormula(formula, displayMode);
  }, [formula, displayMode]);

  return (
    <span 
      className={`inline-block overflow-x-auto select-text ${className}`}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

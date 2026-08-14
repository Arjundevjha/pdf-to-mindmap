import { useMemo } from 'react';
import katex from 'katex';
import 'katex/dist/katex.min.css';

interface MathRendererProps {
  content: string;
  block?: boolean;
  className?: string;
}

/**
 * Renders LaTeX formulas ($...$ or $$...$$) and mixed markdown text containing math expressions
 * synchronously using KaTeX for instant, zero-reflow rendering.
 */
export function MathRenderer({ content, block = false, className = '' }: MathRendererProps) {
  const renderedContent = useMemo(() => {
    if (!content) return '';

    // If pure block formula requested directly
    if (block) {
      try {
        return katex.renderToString(content.trim(), {
          displayMode: true,
          throwOnError: false,
        });
      } catch {
        return content;
      }
    }

    // Process mixed text containing $$block$$ and $inline$ formulas
    try {
      // 1. Replace $$...$$ block math first
      let processed = content.replace(/\$\$([\s\S]+?)\$\$/g, (_, math) => {
        try {
          return `<div class="katex-block-wrapper my-2.5 overflow-x-auto select-text">${katex.renderToString(math.trim(), {
            displayMode: true,
            throwOnError: false,
          })}</div>`;
        } catch {
          return `$$${math}$$`;
        }
      });

      // 2. Replace $...$ inline math
      processed = processed.replace(/\$([^$\n]+?)\$/g, (_, math) => {
        try {

          return `<span class="katex-inline-wrapper select-text">${katex.renderToString(math.trim(), {
            displayMode: false,
            throwOnError: false,
          })}</span>`;
        } catch {
          return `$${math}$`;
        }
      });

      // 3. Format markdown headers and bullets cleanly
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
    try {
      return katex.renderToString(formula.trim(), {
        displayMode,
        throwOnError: false,
      });
    } catch {
      return formula;
    }
  }, [formula, displayMode]);

  return (
    <span 
      className={`inline-block overflow-x-auto select-text ${className}`}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

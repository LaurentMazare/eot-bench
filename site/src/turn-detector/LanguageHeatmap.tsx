'use client';

import { cn } from '../lib/cn';

import type { Budget, TurnDetectorData } from './selection';
import { goodness, heatmapForBudget, metricForBudget, metricKindForBudget } from './selection';

const SUCCESS = '--lk-color-chartSuccess';

function compactValue(value: number, kind: 'cutoff' | 'latency') {
  return kind === 'cutoff' ? `${Math.round(value * 100)}` : `${Math.round(value * 1000)}`;
}

interface LanguageHeatmapProps {
  data: TurnDetectorData;
  budget: Budget;
  activeModelKey?: string | null;
  onActiveModelChange?: (key: string | null) => void;
}

export function LanguageHeatmap({
  data,
  budget,
  activeModelKey,
  onActiveModelChange,
}: LanguageHeatmapProps) {
  const kind = metricKindForBudget(budget);
  const { min, max } = heatmapForBudget(data, budget);
  const isInteractive = onActiveModelChange != null;
  const unit = kind === 'cutoff' ? '% false cut-off' : 'ms latency';

  return (
    <div className="w-full overflow-x-auto">
      <table className="border-separator1 w-full min-w-[760px] table-fixed border-collapse border text-center">
        <caption className="text-fg3 caption-bottom pt-3 text-left text-xs">
          Cell values are {unit}; lower is better, so deeper green is stronger. Empty cells: no
          configuration meets the budget.
        </caption>
        <colgroup>
          <col className="w-44" />
          {data.languages.map((lang) => (
            <col key={lang} />
          ))}
        </colgroup>
        <thead>
          <tr>
            <th className="bg-bg2 text-fg3 border-separator1 sticky left-0 z-10 border px-3 py-2 text-left font-mono text-xs tracking-wider uppercase">
              Model
            </th>
            {data.languages.map((lang) => (
              <th
                key={lang}
                className="border-separator1 text-fg3 border px-1 py-2 font-mono text-xs uppercase"
              >
                {lang}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.models.map((model) => {
            const dimmed = activeModelKey != null && activeModelKey !== model.key;
            return (
              <tr
                key={model.key}
                className={cn(dimmed && 'opacity-40', isInteractive && 'cursor-pointer')}
                onMouseEnter={isInteractive ? () => onActiveModelChange(model.key) : undefined}
                onMouseLeave={isInteractive ? () => onActiveModelChange(null) : undefined}
              >
                <th
                  scope="row"
                  title={model.label}
                  className={cn(
                    'bg-bg2 border-separator1 sticky left-0 z-10 border px-3 py-2 text-left align-middle text-xs font-normal',
                    model.isLiveKit ? 'text-fg0 font-semibold' : 'text-fg1',
                  )}
                >
                  <span className="line-clamp-2 leading-tight">{model.label}</span>
                </th>
                {data.languages.map((lang) => {
                  const value = metricForBudget(data.byLanguage[lang]?.[model.key], budget);
                  if (value === null) {
                    return (
                      <td
                        key={lang}
                        className="border-separator1 bg-bg1 text-fg4 border px-1 py-2 text-center align-middle font-mono text-[11px]"
                      >
                        –
                      </td>
                    );
                  }
                  const alpha = 0.1 + 0.8 * goodness(value, min, max);
                  return (
                    <td
                      key={lang}
                      className="border-separator1 text-fg0 border px-1 py-2 text-center align-middle font-mono text-[11px] tabular-nums"
                      style={{ backgroundColor: `oklch(var(${SUCCESS}) / ${alpha.toFixed(3)})` }}
                      title={`${model.label} · ${lang.toUpperCase()}: ${compactValue(value, kind)}${
                        kind === 'cutoff' ? '% false cut-off' : ' ms'
                      }`}
                    >
                      {compactValue(value, kind)}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

import { Component, computed, inject, signal } from '@angular/core';

import { ApiService } from '../../core/api.service';
import { fmtAbsPct, fmtDate } from '../../core/format';
import { CalibrationReport, ReliabilityBin } from '../../core/models';

@Component({
  selector: 'app-calibration',
  imports: [],
  templateUrl: './calibration.html',
})
export class Calibration {
  private readonly api = inject(ApiService);

  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);
  protected readonly report = signal<CalibrationReport | null>(null);

  protected readonly bestGridPoint = computed(() => {
    const r = this.report();
    if (!r || r.grid_search.length === 0) return null;
    return [...r.grid_search].sort((a, b) => a.loss - b.loss)[0];
  });

  /** Cuando el grid search incluye varios rho, mostramos la columna ρ. */
  protected readonly gridHasRho = computed(() => {
    const r = this.report();
    if (!r) return false;
    return r.grid_search.some((g) => g.dixon_coles_rho !== undefined && g.dixon_coles_rho !== 0);
  });

  protected fmtRho(value: number | undefined | null): string {
    const v = value ?? 0;
    return (v >= 0 ? '+' : '') + v.toFixed(3);
  }

  /** Etiqueta corta del DC para tooltips/badges. */
  protected dcDescription(rho: number | undefined | null): string {
    const v = rho ?? 0;
    if (v === 0) return 'Poisson independiente (sin DC)';
    if (v < 0) return `Dixon-Coles ρ=${v.toFixed(3)} · favorece empates 0-0/1-1`;
    return `Dixon-Coles ρ=+${v.toFixed(3)} · favorece 1-0/0-1`;
  }

  protected readonly accuracyVsRandom = computed(() => {
    const r = this.report();
    if (!r) return 0;
    return r.accuracy_1x2 - 1 / 3;
  });

  /** Para el reliability diagram: todos los bins comparten escala 0-1. */
  protected readonly reliabilityHomeMax = computed(() => 1);
  protected readonly reliabilityOver25Max = computed(() => 1);

  constructor() {
    this.load();
  }

  private load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.api.getLatestCalibration().subscribe({
      next: (r) => {
        this.report.set(r);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(this.formatError(err));
        this.loading.set(false);
      },
    });
  }

  protected reload(): void {
    this.load();
  }

  protected pctOf(value: number): string {
    return `${(value * 100).toFixed(1)}%`;
  }

  protected delta(predicted: number, observed: number): string {
    const d = (observed - predicted) * 100;
    return `${d >= 0 ? '+' : ''}${d.toFixed(1)} pp`;
  }

  protected deltaClass(predicted: number, observed: number): string {
    const d = Math.abs(observed - predicted);
    if (d <= 0.03) return 'text-emerald-400';
    if (d <= 0.07) return 'text-amber-300';
    return 'text-rose-400';
  }

  /** Calidad cualitativa de cada métrica clave. */
  protected accuracyQuality(value: number): { label: string; class: string } {
    if (value >= 0.55) return { label: 'Buena', class: 'text-emerald-400' };
    if (value >= 0.50) return { label: 'Decente', class: 'text-amber-300' };
    return { label: 'Floja', class: 'text-rose-400' };
  }

  protected brierQuality(value: number): { label: string; class: string } {
    // Para 1X2: 0.20 ya es "decente", < 0.18 es bueno
    if (value < 0.19) return { label: 'Buena', class: 'text-emerald-400' };
    if (value < 0.21) return { label: 'Decente', class: 'text-amber-300' };
    return { label: 'Floja', class: 'text-rose-400' };
  }

  protected fmtAbsPct = fmtAbsPct;
  protected fmtDate = fmtDate;

  /** Devuelve el bin como porcentaje legible y datos para la barra. */
  protected binData(b: ReliabilityBin): {
    range: string;
    pred: string;
    obs: string;
    delta: string;
    deltaClass: string;
    width: string;
  } {
    return {
      range: `${(b.lower * 100).toFixed(0)} – ${(b.upper * 100).toFixed(0)}%`,
      pred: this.pctOf(b.mean_predicted),
      obs: this.pctOf(b.observed_frequency),
      delta: this.delta(b.mean_predicted, b.observed_frequency),
      deltaClass: this.deltaClass(b.mean_predicted, b.observed_frequency),
      width: `${(b.observed_frequency * 100).toFixed(0)}%`,
    };
  }

  private formatError(err: unknown): string {
    if (err && typeof err === 'object' && 'status' in err) {
      const status = (err as { status: number }).status;
      if (status === 404) {
        return (
          'Aún no hay reportes de calibración. ' +
          'Genera uno con: python -m backend.tools.calibrate_poisson --save'
        );
      }
    }
    if (err instanceof Error) return err.message;
    return 'Error cargando el reporte de calibración.';
  }
}

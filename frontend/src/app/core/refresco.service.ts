import { Injectable, computed, effect, inject, signal, untracked } from '@angular/core';
import { EMPTY, Subscription, catchError, of, startWith, switchMap, takeWhile, timer } from 'rxjs';

import { ApiService } from './api.service';
import { AuthService } from './auth.service';
import type { RefreshAuto } from './models';

/** Cada cuánto se pregunta si el refresh completo ha terminado (tarda unos minutos). */
const SONDEO_MS = 15_000;

/**
 * Refresh completo al abrir la app. Con sesión (al entrar o al volver con el token guardado) se
 * pide `POST /refresh/auto`: el backend decide si los datos están viejos y, si lo lanza, aquí se
 * consulta cada 15 s hasta que acaba. `terminados` sube al acabar, y la portada recarga partidos
 * y picks. Si algo falla, la app sigue como si no hubiera refresh automático.
 */
@Injectable({ providedIn: 'root' })
export class RefrescoService {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);

  readonly estado = signal<RefreshAuto | null>(null);
  readonly enCurso = computed(() => this.estado()?.en_curso ?? false);
  /** Refrescos terminados mientras la pestaña estaba abierta. */
  readonly terminados = signal(0);

  private sub: Subscription | null = null;

  constructor() {
    effect(() => {
      const conSesion = this.auth.isLoggedIn();
      untracked(() => (conSesion ? this.comprobar() : this.parar()));
    });
  }

  /** Pide al backend que lance el refresh completo si toca (al abrir la app o sin calendario). */
  comprobar(): void {
    this.parar();
    this.sub = this.api
      .ensureRefreshAuto()
      .pipe(
        switchMap((e) =>
          e.en_curso
            ? timer(SONDEO_MS, SONDEO_MS).pipe(
                switchMap(() => this.api.getRefreshAuto()),
                takeWhile((s) => s.en_curso, true),
                startWith(e),
              )
            : of(e),
        ),
        catchError(() => {
          this.estado.set(null);
          return EMPTY;
        }),
      )
      .subscribe((e) => {
        const corria = this.enCurso();
        this.estado.set(e);
        if (corria && !e.en_curso) this.terminados.update((n) => n + 1);
      });
  }

  private parar(): void {
    this.sub?.unsubscribe();
    this.sub = null;
    this.estado.set(null);
  }
}

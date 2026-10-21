import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import {
  ActivatedRouteSnapshot,
  ViewTransitionInfo,
  provideRouter,
  withComponentInputBinding,
  withViewTransitions,
} from '@angular/router';
import { provideHttpClient, withFetch, withInterceptors } from '@angular/common/http';

import { authInterceptor } from './core/auth.interceptor';
import { routes } from './app.routes';
import { movimientoReducido } from './shared/tv-motion';

/** Segmentos de URL de una ruta: `/partido/50` → 2, `/dashboard` → 1. */
function profundidad(ruta: ActivatedRouteSnapshot | null): number {
  let n = 0;
  for (let r = ruta; r; r = r.firstChild) n += r.url.length;
  return n;
}

/**
 * Cortinilla entre páginas (CSS en styles.scss): barre hacia la derecha al entrar en algo y
 * hacia la izquierda al volver. Con "reducir movimiento", cambio seco.
 */
function cortinilla({ transition, from, to }: ViewTransitionInfo): void {
  if (movimientoReducido()) {
    transition.skipTransition();
    return;
  }
  const raiz = document.documentElement;
  raiz.dataset['tvVt'] = profundidad(to) < profundidad(from) ? 'atras' : 'avance';
  const limpiar = () => delete raiz.dataset['tvVt'];
  transition.finished.then(limpiar, limpiar);
}

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(
      routes,
      withComponentInputBinding(),
      withViewTransitions({ skipInitialTransition: true, onViewTransitionCreated: cortinilla }),
    ),
    provideHttpClient(withFetch(), withInterceptors([authInterceptor])),
  ],
};

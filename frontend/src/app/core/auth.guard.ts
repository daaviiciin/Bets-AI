import { inject } from '@angular/core';
import { CanActivateFn, Router, UrlTree } from '@angular/router';

import { AuthService } from './auth.service';

/** Parámetro de /login con la ruta a la que volver tras entrar. */
export const PARAM_VOLVER = 'volver';

/**
 * Ruta interna a la que volver tras el login. Solo rutas propias (`/algo`): nada de
 * `//otro-dominio`, URLs absolutas ni volver a /login.
 */
export function destinoSeguro(url: string | null | undefined): string {
  if (!url || !url.startsWith('/') || url.startsWith('//') || url.startsWith('/\\')) {
    return '/dashboard';
  }
  if (url === '/' || url.startsWith('/login')) return '/dashboard';
  return url;
}

/** UrlTree a /login recordando a dónde iba el usuario. */
export function irALogin(router: Router, volverA: string): UrlTree {
  const destino = destinoSeguro(volverA);
  return router.createUrlTree(['/login'], {
    queryParams: destino === '/dashboard' ? {} : { [PARAM_VOLVER]: destino },
  });
}

/** Toda la app salvo /login: sin sesión válida, a /login?volver=<ruta>. */
export const authGuard: CanActivateFn = (_route, state) => {
  const auth = inject(AuthService);
  auth.pruneExpiredSession();
  if (auth.token() && auth.isLoggedIn()) return true;
  return irALogin(inject(Router), state.url);
};

/** /login solo sin sesión; con sesión, directo a donde iba (o al dashboard). */
export const guestOnlyGuard: CanActivateFn = (route) => {
  const auth = inject(AuthService);
  const router = inject(Router);
  auth.pruneExpiredSession();
  if (auth.token() && auth.isLoggedIn()) {
    return router.parseUrl(destinoSeguro(route.queryParamMap.get(PARAM_VOLVER)));
  }
  return true;
};

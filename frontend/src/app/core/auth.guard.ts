import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import { AuthService } from './auth.service';

/** Rutas sólo usuarios identificados (mis apuestas, guardar combinada en API…). */
export const authGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);
  auth.pruneExpiredSession();
  if (auth.token() && auth.isLoggedIn()) return true;
  return router.createUrlTree(['/']);
};

/** Rutas sólo cuando no hay sesión válida: pantalla inicial de login. */
export const guestOnlyGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);
  auth.pruneExpiredSession();
  if (auth.token() && auth.isLoggedIn()) {
    return router.createUrlTree(['/dashboard']);
  }
  return true;
};

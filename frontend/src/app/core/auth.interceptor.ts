import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';

import { irALogin } from './auth.guard';
import { AuthService } from './auth.service';

/**
 * Añade `Authorization: Bearer <token>` a todas las peticiones (salvo el propio login) y,
 * si la API responde 401 (token caducado, secreto cambiado…), cierra la sesión y lleva a
 * /login recordando la página actual.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const router = inject(Router);
  const esLogin = req.url.toLowerCase().endsWith('/auth/login');
  const token = auth.token();
  const peticion =
    token && !esLogin ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : req;

  return next(peticion).pipe(
    catchError((err: unknown) => {
      if (err instanceof HttpErrorResponse && err.status === 401 && !esLogin) {
        auth.clearSession();
        if (!router.url.startsWith('/login')) {
          void router.navigateByUrl(irALogin(router, router.url));
        }
      }
      return throwError(() => err);
    }),
  );
};

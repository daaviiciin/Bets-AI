import { computed, Injectable, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, EMPTY, map, Observable, switchMap, tap, throwError } from 'rxjs';

import { ApiService } from './api.service';
import type { AuthUserBrief } from './models';

const STORAGE_KEY = 'betsai_jwt';

function decodeJwtExpSeconds(token: string): number | null {
  try {
    const payload = token.split('.')[1];
    if (!payload) return null;
    const base64 = payload.replace(/-/g, '+').replace(/_/g, '/');
    const pad = '='.repeat((4 - (base64.length % 4)) % 4);
    const json = JSON.parse(atob(base64 + pad));
    return typeof json.exp === 'number' ? json.exp : null;
  } catch {
    return null;
  }
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);

  private readonly tokenSig = signal<string | null>(
    typeof localStorage !== 'undefined' ? localStorage.getItem(STORAGE_KEY) : null,
  );
  private readonly userSig = signal<AuthUserBrief | null>(null);

  readonly token = this.tokenSig.asReadonly();
  readonly user = this.userSig.asReadonly();

  /**
   * Token presente en localStorage y no caducado (solo claim ``exp``, sin verificar firma).
   */
  readonly isLoggedIn = computed(() => {
    const raw = this.tokenSig();
    if (!raw) return false;
    const exp = decodeJwtExpSeconds(raw);
    if (exp != null && exp * 1000 <= Date.now()) return false;
    return true;
  });

  /**
   * Limpia el token si ya expiró (útil antes de rutas).
   */
  pruneExpiredSession(): void {
    const raw = this.tokenSig();
    if (!raw) return;
    const exp = decodeJwtExpSeconds(raw);
    if (exp != null && exp * 1000 <= Date.now()) {
      this.clearSession();
    }
  }

  /** Recuperar usuario si el JWT sigue vivo. */
  hydrateFromStorage(): void {
    this.pruneExpiredSession();
    if (!this.tokenSig()) return;
    this.api
      .authMe()
      .pipe(
        tap((u) => this.userSig.set(u)),
        catchError(() => {
          this.clearSession();
          return EMPTY;
        }),
      )
      .subscribe();
  }

  clearSession(): void {
    localStorage.removeItem(STORAGE_KEY);
    this.tokenSig.set(null);
    this.userSig.set(null);
  }

  logout(goLogin = false): void {
    this.clearSession();
    if (goLogin) void this.router.navigateByUrl('/');
  }

  login(username: string, password: string): Observable<void> {
    return this.api.login(username, password).pipe(
      tap((r) => {
        localStorage.setItem(STORAGE_KEY, r.access_token);
        this.tokenSig.set(r.access_token);
      }),
      switchMap(() => this.api.authMe()),
      tap((u) => this.userSig.set(u)),
      map(() => undefined),
      catchError((err) => {
        this.clearSession();
        return throwError(() => err);
      }),
    );
  }
}

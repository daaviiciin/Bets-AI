import { Component, inject, signal } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { ActivatedRoute, Router } from '@angular/router';
import { finalize } from 'rxjs/operators';

import { destinoSeguro, PARAM_VOLVER } from '../../core/auth.guard';
import { AuthService } from '../../core/auth.service';
import { TvLogoRoto } from '../../shared/fx/logo-roto';

/**
 * Entrar. Misma voz que la portada: el rótulo "Bets [AI]" entra en antena (barre con su filete
 * guía y la placa AI barre detrás), un cabezal de escaneo cruza una vez y el formulario barre.
 */
@Component({
  selector: 'app-login',
  imports: [TvLogoRoto],
  templateUrl: './login.html',
  styles: `
    .titulo {
      font-size: clamp(4rem, 13vw, 8.5rem);
      line-height: 1;
    }

    /* Cabezal de escaneo de la portada: una línea de tinta a −8° que cruza una vez. */
    .cabezal {
      position: absolute;
      inset: 0 auto 0 0;
      width: 3px;
      background: var(--color-tv-ink);
      opacity: 0;
      pointer-events: none;
      animation: login-escaneo 600ms var(--ease-out-expo) 40ms;
    }

    @keyframes login-escaneo {
      from {
        opacity: 1;
        transform: translateX(-2vw) skewX(var(--skew));
      }
      85% {
        opacity: 1;
      }
      to {
        opacity: 0;
        transform: translateX(102vw) skewX(var(--skew));
      }
    }

    @media (prefers-reduced-motion: reduce) {
      .cabezal {
        animation: none;
      }
    }
  `,
})
export class LoginPage {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  protected readonly username = signal('');
  protected readonly password = signal('');
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);

  protected submit(): void {
    if (this.busy()) return;
    const u = this.username().trim();
    const p = this.password();
    if (!u || !p) {
      this.error.set('Usuario y contraseña son obligatorios.');
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    const volver = destinoSeguro(this.route.snapshot.queryParamMap.get(PARAM_VOLVER));
    this.auth
      .login(u, p)
      .pipe(finalize(() => this.busy.set(false)))
      .subscribe({
        next: () => void this.router.navigateByUrl(volver),
        error: (err: unknown) => {
          this.password.set('');
          this.error.set(
            err instanceof HttpErrorResponse && err.status === 429
              ? 'Demasiados intentos. Espera un minuto y vuelve a probar.'
              : 'Credenciales incorrectas o error de red.',
          );
        },
      });
  }
}

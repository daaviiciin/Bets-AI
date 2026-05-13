import { Component, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { finalize } from 'rxjs/operators';

import { AuthService } from '../../core/auth.service';

@Component({
  selector: 'app-login',
  imports: [RouterLink],
  templateUrl: './login.html',
})
export class LoginPage {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  protected readonly username = signal('admin');
  protected readonly password = signal('1234');
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
    this.auth
      .login(u, p)
      .pipe(finalize(() => this.busy.set(false)))
      .subscribe({
        next: () => void this.router.navigateByUrl('/dashboard'),
        error: () => this.error.set('Credenciales incorrectas o error de red.'),
      });
  }
}

import { Component, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { AuthService } from './core/auth.service';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App {
  protected readonly year = new Date().getFullYear();
  protected readonly auth = inject(AuthService);

  constructor() {
    this.auth.hydrateFromStorage();
  }

  protected logout(): void {
    this.auth.logout(true);
  }
}

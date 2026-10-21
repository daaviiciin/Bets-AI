import { Component, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { AuthService } from './core/auth.service';
import { BetslipService } from './core/betslip.service';
import { fmtCuota } from './core/format';
import { BetslipPanel } from './shared/betslip-panel';
import { Icon } from './shared/icon';
import { SlipFlight } from './shared/slip-flight';
import { TvCifra } from './shared/tv-motion';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, BetslipPanel, SlipFlight, Icon, TvCifra],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App {
  protected readonly year = new Date().getFullYear();
  protected readonly auth = inject(AuthService);
  protected readonly slip = inject(BetslipService);
  protected readonly fmtCuota = fmtCuota;

  protected readonly nav = [
    { path: '/dashboard', label: 'Partidos' },
    { path: '/mis-apuestas', label: 'Mis apuestas' },
    { path: '/transparencia', label: 'Transparencia' },
  ];

  constructor() {
    this.auth.hydrateFromStorage();
  }

  protected logout(): void {
    this.auth.logout(true);
  }
}

import { Routes } from '@angular/router';

import { authGuard, guestOnlyGuard } from './core/auth.guard';

export const routes: Routes = [
  {
    path: '',
    canActivate: [guestOnlyGuard],
    loadComponent: () => import('./pages/login/login').then((m) => m.LoginPage),
    title: 'Entrar · Bets AI',
  },
  {
    path: 'dashboard',
    loadComponent: () => import('./pages/dashboard/dashboard').then((m) => m.Dashboard),
    title: 'Dashboard · Bets AI',
  },
  {
    path: 'transparencia',
    loadComponent: () =>
      import('./pages/transparencia/transparencia').then((m) => m.Transparencia),
    title: 'Transparencia · Bets AI',
  },
  {
    path: 'estadisticas',
    loadComponent: () => import('./pages/estadisticas/estadisticas').then((m) => m.Estadisticas),
    title: 'Estadísticas · Bets AI',
  },
  {
    path: 'login',
    redirectTo: '',
    pathMatch: 'full',
  },
  {
    path: 'mis-apuestas',
    canActivate: [authGuard],
    loadComponent: () => import('./pages/mis-apuestas/mis-apuestas').then((m) => m.MisApuestas),
    title: 'Mis apuestas · Bets AI',
  },
  { path: '**', redirectTo: '' },
];


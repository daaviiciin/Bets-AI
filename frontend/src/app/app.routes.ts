import { Routes } from '@angular/router';

import { authGuard, guestOnlyGuard } from './core/auth.guard';

/**
 * Todo está tras el login: sin sesión, cualquier ruta lleva a /login?volver=<ruta> y, al
 * entrar, se vuelve allí.
 */
export const routes: Routes = [
  {
    path: 'login',
    canActivate: [guestOnlyGuard],
    loadComponent: () => import('./pages/login/login').then((m) => m.LoginPage),
    title: 'Entrar · Bets AI',
  },
  {
    path: '',
    canActivateChild: [authGuard],
    children: [
      { path: '', redirectTo: 'dashboard', pathMatch: 'full' },
      {
        path: 'dashboard',
        loadComponent: () => import('./pages/dashboard/dashboard').then((m) => m.Dashboard),
        title: 'Partidos · Bets AI',
      },
      {
        // La página pone su propio título ("Sevilla – Real Betis · Bets AI") al cargar.
        path: 'partido/:id',
        loadComponent: () =>
          import('./pages/match-detail/match-detail').then((m) => m.MatchDetail),
        title: 'Partido · Bets AI',
      },
      {
        path: 'transparencia',
        loadComponent: () =>
          import('./pages/transparencia/transparencia').then((m) => m.Transparencia),
        title: 'Transparencia · Bets AI',
      },
      {
        path: 'mis-apuestas',
        loadComponent: () =>
          import('./pages/mis-apuestas/mis-apuestas').then((m) => m.MisApuestas),
        title: 'Mis apuestas · Bets AI',
      },
      { path: '**', redirectTo: 'dashboard' },
    ],
  },
];

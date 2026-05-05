import { Routes } from '@angular/router';

export const routes: Routes = [
  {
    path: '',
    loadComponent: () => import('./pages/dashboard/dashboard').then((m) => m.Dashboard),
    title: 'Dashboard · Bets AI',
  },
  {
    path: 'recommendations',
    loadComponent: () =>
      import('./pages/recommendations/recommendations').then((m) => m.Recommendations),
    title: 'Recomendaciones · Bets AI',
  },
  {
    path: 'value-bets',
    loadComponent: () => import('./pages/value-bets/value-bets').then((m) => m.ValueBets),
    title: 'Value Bets · Bets AI',
  },
  {
    path: 'matches',
    loadComponent: () => import('./pages/matches/matches').then((m) => m.Matches),
    title: 'Partidos · Bets AI',
  },
  {
    path: 'calibracion',
    loadComponent: () => import('./pages/calibration/calibration').then((m) => m.Calibration),
    title: 'Calibración · Bets AI',
  },
  {
    path: 'matches/:id',
    loadComponent: () =>
      import('./pages/match-detail/match-detail').then((m) => m.MatchDetail),
    title: 'Partido · Bets AI',
  },
  { path: '**', redirectTo: '' },
];

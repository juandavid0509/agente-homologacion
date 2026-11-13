import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import LoginModal from '../src/components/LoginModal';
import { BrowserRouter } from 'react-router-dom';

describe('LoginModal', () => {
  it('renderiza los campos de correo y contraseña', () => {
    render(<BrowserRouter><LoginModal /></BrowserRouter>);
    expect(screen.getByPlaceholderText('Correo Electrónico')).toBeDefined();
    expect(screen.getByPlaceholderText('Contraseña')).toBeDefined();
  });
});
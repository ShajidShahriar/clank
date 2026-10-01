import React from 'react';

interface ButtonProps {
  label: string;
}

/** A button. */
export function Button({ label }: ButtonProps) {
  return <button className="btn">{label}</button>;
}

export const Card: React.FC<ButtonProps> = ({ label }) => {
  return <div>{label}</div>;
};

export default class Page extends React.Component<ButtonProps> {
  state = { open: false };
  toggle = () => { this.setState({ open: !this.state.open }); };
  render() {
    return <Button label="hi" />;
  }
}

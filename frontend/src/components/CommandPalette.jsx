import { Command } from 'cmdk';
import { Moon, Plus, Settings, Sun, User } from "lucide-react";
import './CommandPalette.css';

export default function CommandPalette({
  open,
  onOpenChange,
  onNewChat,
  onToggleTheme,
  onNavigate,
  role,
  darkMode
}) {
  if (!open) return null;

  const runAction = (fn) => {
    fn();
    onOpenChange(false);
  };

  return (
    <div className="cmdk-overlay" onClick={() => onOpenChange(false)}>
      <div className="cmdk-container" onClick={(e) => e.stopPropagation()}>
        <Command label="Command Menu">
          <Command.Input placeholder="Type a command..." autoFocus />
          <Command.List>
            <Command.Empty>No results found.</Command.Empty>

            <Command.Group heading="Actions">
              <Command.Item onSelect={() => runAction(onNewChat)}>
                <Plus size={14} />
                <span>New Chat</span>
              </Command.Item>

              <Command.Item onSelect={() => runAction(onToggleTheme)}>
                {darkMode ? <Sun size={14} /> : <Moon size={14} />}
                <span>Toggle {darkMode ? 'Light' : 'Dark'} Mode</span>
              </Command.Item>

              <Command.Item onSelect={() => runAction(() => onNavigate('/profile'))}>
                <User size={14} />
                <span>Open Profile</span>
              </Command.Item>

              {role === 'admin' && (
                <Command.Item onSelect={() => runAction(() => onNavigate('/admin'))}>
                  <Settings size={14} />
                  <span>Open Admin Dashboard</span>
                </Command.Item>
              )}
            </Command.Group>
          </Command.List>
        </Command>
      </div>
    </div>
  );
}

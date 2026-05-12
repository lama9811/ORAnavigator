import { Command } from 'cmdk';
import { FaPlus } from "@react-icons/all-files/fa/FaPlus";
import { FaMoon } from "@react-icons/all-files/fa/FaMoon";
import { FaSun } from "@react-icons/all-files/fa/FaSun";
import { FaUser } from "@react-icons/all-files/fa/FaUser";
import { FaBook } from "@react-icons/all-files/fa/FaBook";
import { FaCog } from "@react-icons/all-files/fa/FaCog";
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
                <FaPlus size={14} />
                <span>New Chat</span>
              </Command.Item>

              <Command.Item onSelect={() => runAction(onToggleTheme)}>
                {darkMode ? <FaSun size={14} /> : <FaMoon size={14} />}
                <span>Toggle {darkMode ? 'Light' : 'Dark'} Mode</span>
              </Command.Item>

              <Command.Item onSelect={() => runAction(() => onNavigate('/profile'))}>
                <FaUser size={14} />
                <span>Open Profile</span>
              </Command.Item>

              <Command.Item onSelect={() => runAction(() => onNavigate('/curriculum'))}>
                <FaBook size={14} />
                <span>Open Curriculum</span>
              </Command.Item>

              {role === 'admin' && (
                <Command.Item onSelect={() => runAction(() => onNavigate('/admin'))}>
                  <FaCog size={14} />
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

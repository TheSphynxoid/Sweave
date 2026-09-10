/**
 * ConfirmDeleteDialog — shared destructive-action confirmation.
 *
 * A modal confirmation dialog used before irreversible deletions (e.g.
 * session deletion): names the target in the body, Cancel/Escape/backdrop
 * all dismiss without action, and the confirm button is disabled while the
 * parent mutation is in flight to prevent double-submits.
 */
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function ConfirmDeleteDialog({
  open,
  title,
  body,
  pending = false,
  onOpenChange,
  onConfirm,
  confirmLabel = "Delete",
  testId,
}: {
  open: boolean;
  title: string;
  body: string;
  pending?: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  confirmLabel?: string;
  testId?: string;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid={testId}
        className="max-w-md"
        onEscapeKeyDown={() => onOpenChange(false)}
        onPointerDownOutside={() => onOpenChange(false)}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{body}</DialogDescription>
        </DialogHeader>
        <DialogFooter className="gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={pending}
            onClick={() => onOpenChange(false)}
            data-testid={testId ? `${testId}-cancel` : undefined}
          >
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={pending}
            onClick={onConfirm}
            data-testid={testId ? `${testId}-confirm` : undefined}
          >
            {pending && <Loader2 size={14} className="animate-spin" />}
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

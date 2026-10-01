import { Container } from "@/components/ui/page";
import { NotFoundState } from "@/components/ui/states";

export default function NotFound() {
  return (
    <Container size="md" className="py-24">
      <NotFoundState />
    </Container>
  );
}

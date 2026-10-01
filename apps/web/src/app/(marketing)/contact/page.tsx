import { Suspense } from "react";
import { ContactForm } from "@/components/marketing/contact-form";
import { ContentPage } from "@/components/marketing/content-page";

export const metadata = { title: "Contact", description: "Talk to sales, book a demo, request a security review or a private deployment." };

export default function Page() {
  return (
    <ContentPage eyebrow="Contact" title="Talk to the team" lede="Sales questions, guided demos, security reviews and private deployments.">
      <Suspense>
        <ContactForm />
      </Suspense>
    </ContentPage>
  );
}

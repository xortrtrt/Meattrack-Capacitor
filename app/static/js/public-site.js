import { bindLandingExperience, bindLandingProductModal } from "./landing-experience.js";
import { bindMobileMenu } from "./public-navigation.js";
import { bindVisitorChat } from "./visitor-chat.js";

bindMobileMenu("[data-mobile-nav-toggle]", "[data-mobile-nav]");
bindLandingExperience();
bindLandingProductModal();
bindVisitorChat();


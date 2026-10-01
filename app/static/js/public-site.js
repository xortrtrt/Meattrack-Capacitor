import { bindLandingExperience, bindLandingProductModal } from "./landing-experience.js";
import { bindMobileMenu } from "./public-navigation.js";
import { bindVisitorChat } from "./visitor-chat.js?v=20261002-chat-interactions-1";

bindMobileMenu("[data-mobile-nav-toggle]", "[data-mobile-nav]");
bindLandingExperience();
bindLandingProductModal();
bindVisitorChat();

